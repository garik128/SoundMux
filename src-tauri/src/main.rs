#![cfg_attr(windows, windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::{io::{BufRead, BufReader, Read, Write}, path::{Path, PathBuf}, process::{Command, Stdio}, sync::{atomic::{AtomicBool, Ordering}, Arc}, time::Duration};
use tauri::{Emitter, Manager};
mod preferences;
mod shell;
use preferences::{get_dialog_directory, remember_dialog_directory, Preferences};

mod jobs;
use jobs::Jobs;
mod applog;
mod cache;
mod settings;
use settings::{Load, Settings};

#[cfg(windows)]
struct ProcessTree(isize);
#[cfg(windows)]
impl ProcessTree {
    fn attach(child: &std::process::Child) -> Result<Self, String> {
        use std::{mem::size_of, os::windows::io::AsRawHandle};
        use windows_sys::Win32::{Foundation::CloseHandle, System::JobObjects::*};
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() { return Err(std::io::Error::last_os_error().to_string()); }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle, JobObjectExtendedLimitInformation, &info as *const _ as _, size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32) == 0
                || AssignProcessToJobObject(handle, child.as_raw_handle() as _) == 0 {
                let error = std::io::Error::last_os_error().to_string(); CloseHandle(handle); return Err(error);
            }
            Ok(Self(handle as isize))
        }
    }
    fn terminate(&self) {
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            TerminateJobObject(self.0 as _, 1);
            // Wait for descendants too before dropping TempDir and removing media files.
            for _ in 0..200 {
                let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = std::mem::zeroed();
                let ok = QueryInformationJobObject(self.0 as _, JobObjectBasicAccountingInformation, &mut info as *mut _ as _, std::mem::size_of_val(&info) as u32, std::ptr::null_mut());
                if ok == 0 || info.ActiveProcesses == 0 { break; }
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }
}
#[cfg(windows)]
impl Drop for ProcessTree {
    fn drop(&mut self) { self.terminate(); unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0 as _); } }
}

/// Default cache next to the program (portable layout), then the per-user cache when that is read-only.
fn cache_locations(app: &tauri::AppHandle, root: &Path) -> Result<(PathBuf, PathBuf), String> {
    if cfg!(debug_assertions) { return Ok((root.join(".local/cache"), root.join(".local/cache"))); }
    Ok((root.join("cache"), app.path().app_cache_dir().map_err(|e| e.to_string())?))
}

/// The service log: next to the program (portable layout), or in the per-user folder when that is read-only.
fn log_location(app: &tauri::AppHandle, root: &Path) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) { return Ok(root.join(".local/logs")); }
    let beside = root.join("logs");
    if settings::usable(&beside) { return Ok(beside); }
    app.path().app_log_dir().map_err(|e| e.to_string())
}

/// Debug builds work from the project folder. The path is compiled in only for debug builds, so a
/// release EXE does not carry the developer's folder names.
#[cfg(debug_assertions)]
fn program_root(_app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf())
}

#[cfg(not(debug_assertions))]
fn program_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    app.path().resource_dir().map(plain_path).map_err(|e| e.to_string())
}

/// Windows can report a drive path in the verbatim form `\\?\D:\...`. That prefix is noise in the
/// interface and in tool arguments. Network paths (`\\?\UNC\...`) are left as they are.
fn plain_path(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy().into_owned();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if rest.as_bytes().get(1) == Some(&b':') => PathBuf::from(rest),
        _ => path,
    }
}

#[tauri::command]
fn get_app_settings(app: tauri::AppHandle, state: tauri::State<'_, Settings>) -> Result<Value, String> {
    let (default_cache, fallback_cache) = cache_locations(&app, &program_root(&app)?)?;
    let (effective, skipped) = state.cache_dir(&default_cache, &fallback_cache);
    let log_dir = log_location(&app, &program_root(&app)?)?;
    Ok(json!({"cacheDir": effective, "cacheCustom": state.custom_cache().is_some() && !skipped, "cacheSkipped": skipped, "cacheDefault": default_cache, "load": state.load_mode(), "logDir": log_dir}))
}

#[tauri::command]
fn set_cache_dir(state: tauri::State<'_, Settings>, path: Option<PathBuf>) -> Result<(), String> {
    state.set_cache_dir(path)
}

#[tauri::command]
fn set_load(state: tauri::State<'_, Settings>, load: Load) -> Result<(), String> {
    state.set_load(load).map_err(|e| format!("Не удалось сохранить настройку: {e}"))
}

fn config(app: &tauri::AppHandle) -> Result<(Value, PathBuf), String> {
    let root = program_root(app)?;
    let config_path = root.join(".local/tools.json");
    let mut value: Value = if config_path.exists() {
        serde_json::from_slice(&std::fs::read(config_path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?
    } else { json!({"python": root.join("runtime/python/python.exe"), "ffmpeg": root.join("bin/ffmpeg.exe"), "ffprobe": root.join("bin/ffprobe.exe"), "mkvmerge": root.join("bin/mkvmerge.exe"), "mkvmergeLegacy": root.join("bin/legacy/mkvmerge.exe")}) };
    let log_dir = log_location(app, &root)?;
    for key in ["python", "ffmpeg", "ffprobe", "mkvmerge"] {
        if !Path::new(value[key].as_str().unwrap_or("")).is_file() {
            applog::write(&log_dir, "ERROR", &format!("component {key} not found: {}", value[key]), "");
            return Err(format!("Не найден компонент {key}. Проверьте комплект файлов программы."));
        }
    }
    let (default_cache, fallback_cache) = cache_locations(app, &root)?;
    let settings = app.state::<Settings>();
    let (cache, _) = settings.cache_dir(&default_cache, &fallback_cache);
    std::fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
    value["cache"] = json!(cache);
    value["load"] = json!(settings.load_mode());
    value["logDir"] = json!(log_dir);
    value["version"] = json!(env!("CARGO_PKG_VERSION"));
    value["analyzer"] = json!(root.join("backend/vendor/audio_sync.py"));
    Ok((value, root.join("backend/worker.py")))
}

fn run_worker(app: tauri::AppHandle, id: String, action: String, payload: Value, cancelled: Arc<AtomicBool>) -> Result<Value, String> {
    if cancelled.load(Ordering::SeqCst) { return Err("Отменено".into()); }
    let (mut config, worker) = config(&app)?;
    let work = tempfile::Builder::new().prefix("job-").tempdir_in(config["cache"].as_str().unwrap()).map_err(|e| e.to_string())?;
    config["work"] = json!(work.path());
    let build_work = if action == "build" {
        let directory = payload["outputDirectory"].as_str().filter(|s| !s.is_empty()).map(PathBuf::from)
            .or_else(|| payload["movie"]["nativePath"].as_str().and_then(|p| Path::new(p).parent()).map(Path::to_path_buf))
            .ok_or("Выберите папку результата.")?;
        Some(tempfile::Builder::new().prefix(".soundmux-").tempdir_in(directory).map_err(|e| format!("Нет доступа к папке результата: {e}"))?)
    } else { None };
    if let Some(folder) = &build_work {
        config["buildWork"] = json!(folder.path());
        #[cfg(windows)] {
            use std::os::windows::{ffi::OsStrExt, fs::MetadataExt};
            let path: Vec<u16> = folder.path().as_os_str().encode_wide().chain(Some(0)).collect();
            let attributes = folder.path().metadata().map_err(|e| e.to_string())?.file_attributes();
            // Cosmetic only: normal cancellation still deletes this temporary folder.
            unsafe { windows_sys::Win32::Storage::FileSystem::SetFileAttributesW(path.as_ptr(), attributes | 2); }
        }
    }
    let mut command = Command::new(config["python"].as_str().unwrap());
    command.args(["-I", "-X", "utf8", "-u"]).arg(worker).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    // -I excludes user site-packages and PYTHONPATH. The worker adds only its own directory.
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    let log_dir = PathBuf::from(config["logDir"].as_str().unwrap());
    let mut child = command.spawn().map_err(|e| { applog::write(&log_dir, "ERROR", &format!("{action}: cannot start the worker: {e}"), ""); format!("Не удалось запустить обработку: {e}") })?;
    #[cfg(windows)] let tree = match ProcessTree::attach(&child) { Ok(tree) => tree, Err(e) => { let _ = child.kill(); let _ = child.wait(); return Err(e); } };
    let request = json!({"action": action, "payload": payload, "config": config});
    let send = child.stdin.take().unwrap().write_all(format!("{request}\n").as_bytes());
    if let Err(error) = send { let _ = child.kill(); let _ = child.wait(); return Err(error.to_string()); }
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let events_app = app.clone(); let event_id = id.clone();
    let reader = std::thread::spawn(move || {
        let mut result = None;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Ok(event) = serde_json::from_str::<Value>(&line) {
                match event["type"].as_str() {
                    Some("progress") => { let _ = events_app.emit("media-progress", json!({"taskId": event_id, "label": event["label"], "percent": event["percent"], "etaSeconds": event["etaSeconds"]})); },
                    Some("result") => result = Some(Ok(event["value"].clone())),
                    Some("error") => result = Some(Err(event.to_string())),
                    _ => {}
                }
            }
        }
        result
    });
    let errors = std::thread::spawn(move || { let mut value = String::new(); let _ = BufReader::new(stderr).read_to_string(&mut value); value });
    let status = loop {
        if cancelled.load(Ordering::SeqCst) {
            #[cfg(windows)] tree.terminate();
            #[cfg(not(windows))] { let _ = child.kill(); }
            let _ = child.wait(); break None;
        }
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => std::thread::sleep(Duration::from_millis(30)),
            Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error.to_string()); }
        }
    };
    let output = reader.join().ok().flatten(); let detail = errors.join().unwrap_or_default();
    if status.is_none() { applog::write(&log_dir, "INFO", &format!("{action}: cancelled"), ""); return Err("Отменено".into()); }
    // A failure the worker reports itself is already in the log; here are only the ones it could not report.
    let output = match output {
        Some(result) => result,
        None => { applog::write(&log_dir, "ERROR", &format!("{action}: the worker ended without a result"), &detail); Err(format!("Обработка завершилась без результата. {detail}")) }
    }?;
    if !status.unwrap().success() { applog::write(&log_dir, "ERROR", &format!("{action}: the worker exited with an error"), &detail); return Err(format!("Обработка завершилась с ошибкой. {detail}")); }
    if action == "preview" {
        if let Some(path) = output["path"].as_str() { app.asset_protocol_scope().allow_file(path).map_err(|e| e.to_string())?; }
    }
    Ok(output)
}

#[tauri::command]
async fn media_run(app: tauri::AppHandle, state: tauri::State<'_, Jobs>, task_id: String, action: String, payload: Value) -> Result<Value, String> {
    if !["probe", "analyze", "preview", "build"].contains(&action.as_str()) { return Err("Неизвестное действие".into()); }
    let flag = state.0.lock().unwrap().register(task_id.clone())?;
    let id = task_id.clone(); let handle = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || run_worker(handle, id, action, payload, flag)).await.map_err(|e| e.to_string()).and_then(|value| value);
    state.0.lock().unwrap().running.remove(&task_id);
    result
}

#[tauri::command]
fn media_cancel(state: tauri::State<'_, Jobs>, task_id: String) {
    state.0.lock().unwrap().cancel(task_id);
}

#[tauri::command]
async fn clear_media_cache(app: tauri::AppHandle) -> Result<u64, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<Jobs>();
        // Registration uses the same lock, so a worker cannot start during deletion.
        let jobs = state.0.lock().unwrap();
        if !jobs.running.is_empty() { return Err("Дождитесь завершения обработки перед очисткой кэша.".into()); }
        let (value, _) = config(&app)?;
        cache::clear(Path::new(value["cache"].as_str().unwrap()))
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn open_link(url: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || shell::open_site(&url)).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn open_log_dir(app: tauri::AppHandle) -> Result<(), String> {
    let dir = log_location(&app, &program_root(&app)?)?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("Не удалось создать папку журнала: {e}"))?;
    tauri::async_runtime::spawn_blocking(move || shell::open_directory(&dir)).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn open_result(path: String, folder: bool) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || shell::open_result(Path::new(&path), folder)).await.map_err(|e| e.to_string())?
}

fn main() {
    tauri::Builder::default().manage(Jobs::default()).plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_window_state::Builder::default()
            .with_state_flags(tauri_plugin_window_state::StateFlags::SIZE | tauri_plugin_window_state::StateFlags::POSITION | tauri_plugin_window_state::StateFlags::MAXIMIZED).build())
        .setup(|app| {
            app.manage(Preferences::load(app.path().app_config_dir()?.join("dialog-history.json")));
            app.manage(Settings::load(app.path().app_config_dir()?.join("settings.json")));
            if let Ok(dir) = program_root(app.handle()).and_then(|root| log_location(app.handle(), &root)) {
                applog::write(&dir, "INFO", &format!("SoundMux {} started", env!("CARGO_PKG_VERSION")), "");
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![media_run, media_cancel, clear_media_cache, open_result, open_link, open_log_dir, get_dialog_directory, remember_dialog_directory, get_app_settings, set_cache_dir, set_load])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<Jobs>();
                let jobs = state.0.lock().unwrap();
                if !jobs.running.is_empty() {
                    api.prevent_close();
                    for flag in jobs.running.values() { flag.store(true, Ordering::SeqCst); }
                    let app = window.app_handle().clone();
                    // Keep Rust alive until workers exit and their TempDirs are removed.
                    std::thread::spawn(move || loop {
                        if app.state::<Jobs>().0.lock().unwrap().running.is_empty() { app.exit(0); break; }
                        std::thread::sleep(Duration::from_millis(30));
                    });
                }
            }
        })
        .run(tauri::generate_context!()).expect("Unable to start SoundMux");
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use windows_sys::Win32::{Foundation::CloseHandle, System::Threading::{OpenProcess, WaitForSingleObject}};

    #[test]
    fn verbatim_drive_prefix_is_removed_and_other_paths_are_kept() {
        assert_eq!(plain_path(PathBuf::from(r"\\?\D:\Видео\SoundMux")), PathBuf::from(r"D:\Видео\SoundMux"));
        assert_eq!(plain_path(PathBuf::from(r"D:\Видео\SoundMux")), PathBuf::from(r"D:\Видео\SoundMux"));
        assert_eq!(plain_path(PathBuf::from(r"\\?\UNC\server\share")), PathBuf::from(r"\\?\UNC\server\share"));
    }

    #[test]
    fn cancellation_terminates_python_and_its_descendants() {
        use std::os::windows::process::CommandExt;
        let python = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join(".runtime/python/python.exe");
        let mut child = Command::new(&python).args(["-I", "-u", "-c", "import subprocess,sys;sys.stdin.readline();p=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)']);print(p.pid,flush=True);p.wait()"])
            .stdin(Stdio::piped()).stdout(Stdio::piped()).creation_flags(0x08000000).spawn().unwrap();
        let tree = ProcessTree::attach(&child).unwrap();
        child.stdin.take().unwrap().write_all(b"start\n").unwrap();
        let mut line = String::new(); BufReader::new(child.stdout.take().unwrap()).read_line(&mut line).unwrap();
        let pid: u32 = line.trim().parse().unwrap();
        let process = unsafe { OpenProcess(0x00100000, 0, pid) };
        assert!(!process.is_null());
        tree.terminate();
        assert!(!child.wait().unwrap().success());
        assert_eq!(unsafe { WaitForSingleObject(process, 2000) }, 0, "Descendant is still running");
        unsafe { CloseHandle(process); }
    }
}
