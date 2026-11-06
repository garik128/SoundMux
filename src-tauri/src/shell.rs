use std::path::{Path, PathBuf};

/// The only site the interface may open. A link from the page is passed to Windows only when it points here.
pub const SITE: &str = "https://perevodman.com/";

fn site_target(url: &str) -> Result<&str, String> {
    if url.starts_with(SITE) && !url.chars().any(|c| c.is_whitespace() || c.is_control()) { Ok(url) } else { Err("Эту ссылку открыть нельзя.".into()) }
}

fn shell_path(path: &Path) -> PathBuf {
    let raw = path.to_string_lossy();
    if let Some(unc) = raw.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{unc}"))
    } else {
        PathBuf::from(raw.strip_prefix(r"\\?\").unwrap_or(&raw))
    }
}

fn target(path: &Path, folder: bool) -> Result<PathBuf, String> {
    let path = path.canonicalize().map_err(|e| format!("Файл результата недоступен. Проверьте, что он не был перемещён. {e}"))?;
    if !path.is_file() || !path.extension().and_then(|s| s.to_str()).is_some_and(|s| s.eq_ignore_ascii_case("mkv")) {
        return Err("Файл результата не найден. Сохраните фильм ещё раз.".into());
    }
    let destination = if folder { path.parent().ok_or("Папка результата не найдена.")? } else { &path };
    Ok(shell_path(destination))
}

fn check_result(code: isize, folder: bool) -> Result<(), String> {
    if code > 32 { return Ok(()); }
    let action = if folder { "папку результата" } else { "фильм" };
    let hint = match code {
        2 | 3 => "Проверьте, что файл и папка не были перемещены.",
        5 => "Проверьте доступ к папке результата.",
        27 | 31 if !folder => "Выберите в Windows приложение для воспроизведения файлов MKV.",
        _ => "Попробуйте ещё раз или откройте результат из Проводника.",
    };
    Err(format!("Не удалось открыть {action}. {hint} (Код Windows: {code})"))
}

/// Hand a file, folder or web address to Windows as a Unicode string (never as a command line).
#[cfg(windows)]
fn shell_execute(destination: &std::ffi::OsStr) -> Result<isize, String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::{System::Com::{CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE}, UI::{Shell::ShellExecuteW, WindowsAndMessaging::SW_SHOWNORMAL}};
    let wide: Vec<u16> = destination.encode_wide().chain(Some(0)).collect();
    let verb: Vec<u16> = "open".encode_utf16().chain(Some(0)).collect();
    unsafe {
        let initialized = CoInitializeEx(std::ptr::null(), (COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE) as u32);
        if initialized < 0 { return Err(format!("Не удалось подготовить открытие. Код Windows: {initialized:#x}")); }
        let result = ShellExecuteW(std::ptr::null_mut(), verb.as_ptr(), wide.as_ptr(), std::ptr::null(), std::ptr::null(), SW_SHOWNORMAL) as isize;
        CoUninitialize();
        Ok(result)
    }
}

#[cfg(windows)]
pub fn open_result(path: &Path, folder: bool) -> Result<(), String> {
    // Pass the directory/document as a Unicode path, not an Explorer command
    // line. This avoids /select parsing and quoting errors for spaces/commas.
    let destination = target(path, folder)?;
    check_result(shell_execute(destination.as_os_str())?, folder)
}

#[cfg(windows)]
pub fn open_site(url: &str) -> Result<(), String> {
    let code = shell_execute(std::ffi::OsStr::new(site_target(url)?))?;
    if code > 32 { Ok(()) } else { Err(format!("Не удалось открыть сайт в браузере. (Код Windows: {code})")) }
}

/// Open a folder in Explorer (the log folder).
#[cfg(windows)]
pub fn open_directory(path: &Path) -> Result<(), String> {
    let path = path.canonicalize().map_err(|e| format!("Папка недоступна: {e}"))?;
    if !path.is_dir() { return Err("Папка не найдена.".into()); }
    let code = shell_execute(shell_path(&path).as_os_str())?;
    if code > 32 { Ok(()) } else { Err(format!("Не удалось открыть папку. (Код Windows: {code})")) }
}

#[cfg(not(windows))]
pub fn open_directory(_path: &Path) -> Result<(), String> {
    Err("Открытие папки доступно в Windows.".into())
}

#[cfg(not(windows))]
pub fn open_result(_path: &Path, _folder: bool) -> Result<(), String> {
    Err("Открытие результата доступно в Windows.".into())
}

#[cfg(not(windows))]
pub fn open_site(_url: &str) -> Result<(), String> {
    Err("Открытие сайта доступно в Windows.".into())
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[test]
    fn folder_action_opens_parent_of_unicode_result_without_command_line_arguments() {
        let root = tempfile::tempdir().unwrap();
        let directory = root.path().join("Результаты, тест [2026]");
        std::fs::create_dir(&directory).unwrap();
        let file = directory.join("Фильм с озвучкой.NEW.MKV");
        std::fs::write(&file, b"test").unwrap();
        assert_eq!(target(&file, true).unwrap(), shell_path(&directory.canonicalize().unwrap()));
        assert_eq!(target(&file, false).unwrap(), shell_path(&file.canonicalize().unwrap()));
    }

    #[test]
    fn only_the_project_site_can_be_opened() {
        assert!(site_target("https://perevodman.com/").is_ok());
        assert!(site_target("https://perevodman.com/page?x=1").is_ok());
        for bad in [
            "https://perevodman.com", "https://perevodman.com.evil.example/", "http://perevodman.com/", "https://evil.example/https://perevodman.com/",
            "file:///C:/Windows/System32/cmd.exe", r"C:\Windows\System32\cmd.exe", "https://perevodman.com/ x", "https://perevodman.com/\n",
        ] {
            assert!(site_target(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn shell_failures_are_reported_instead_of_claiming_success() {
        assert!(check_result(33, true).is_ok());
        assert!(check_result(5, true).unwrap_err().contains("доступ"));
        assert!(check_result(31, false).unwrap_err().contains("приложение"));
        assert!(check_result(2, true).is_err());
    }
}
