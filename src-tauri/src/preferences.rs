use serde::{Deserialize, Serialize};
use std::{io::Write, path::{Path, PathBuf}, sync::Mutex};

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct DialogHistory {
    movie: Option<PathBuf>,
    audio: Option<PathBuf>,
    output: Option<PathBuf>,
}

impl DialogHistory {
    fn directory(&self, kind: &str) -> Option<PathBuf> {
        let preferred = match kind { "movie" => &self.movie, "audio" => &self.audio, "output" => &self.output, _ => return None };
        [preferred, &self.movie, &self.audio].into_iter().filter_map(|path| path.as_ref()).find(|path| path.is_dir()).cloned()
    }

    fn remember(&mut self, kind: &str, path: &Path, directory: bool) -> Result<(), String> {
        let folder = if directory { path.to_path_buf() } else { path.parent().ok_or("Не удалось определить папку файла.")?.to_path_buf() };
        if !folder.is_absolute() { return Err("Папка должна быть указана полным путём.".into()); }
        match kind {
            "movie" => self.movie = Some(folder),
            "audio" => self.audio = Some(folder),
            "output" => self.output = Some(folder),
            _ => return Err("Неизвестный вид файла.".into()),
        }
        Ok(())
    }
}

pub struct Preferences {
    path: PathBuf,
    history: Mutex<DialogHistory>,
}

impl Preferences {
    pub fn load(path: PathBuf) -> Self {
        let history = std::fs::read(&path).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
        Self { path, history: Mutex::new(history) }
    }

    fn remember(&self, kind: &str, path: &Path, directory: bool) -> Result<(), String> {
        let mut current = self.history.lock().unwrap();
        let mut updated = current.clone();
        updated.remember(kind, path, directory)?;
        let data = serde_json::to_vec_pretty(&updated).map_err(|e| e.to_string())?;
        write_atomic(&self.path, &data)?;
        *current = updated;
        Ok(())
    }
}

/// Write beside the target and rename, so a crash never leaves a half-written settings file.
pub fn write_atomic(path: &Path, data: &[u8]) -> Result<(), String> {
    let parent = path.parent().ok_or("Папка настроек недоступна.")?;
    std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    temporary.write_all(data).map_err(|e| e.to_string())?;
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(path).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub async fn get_dialog_directory(state: tauri::State<'_, Preferences>, kind: String) -> Result<Option<PathBuf>, String> {
    let history = state.history.lock().unwrap().clone();
    // A disconnected network folder must not block the UI thread.
    tauri::async_runtime::spawn_blocking(move || history.directory(&kind)).await.map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn remember_dialog_directory(state: tauri::State<'_, Preferences>, kind: String, path: PathBuf, directory: bool) -> Result<(), String> {
    state.remember(&kind, &path, directory).map_err(|e| format!("Не удалось запомнить папку: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folders_survive_restart_and_remain_independent() {
        let root = tempfile::tempdir().unwrap();
        let movie = root.path().join("Фильмы [2026]"); let audio = root.path().join("Озвучка");
        std::fs::create_dir(&movie).unwrap(); std::fs::create_dir(&audio).unwrap();
        let settings = root.path().join("preferences.json");
        let initial = Preferences::load(settings.clone());
        initial.remember("movie", &movie.join("Фильм.mkv"), false).unwrap();
        initial.remember("audio", &audio.join("Озвучка.ac3"), false).unwrap();
        // Replacing an existing preferences file must remain atomic and preserve other fields.
        initial.remember("output", root.path(), true).unwrap();
        let restarted = Preferences::load(settings);
        let history = restarted.history.lock().unwrap();
        assert_eq!(history.directory("movie"), Some(movie));
        assert_eq!(history.directory("audio"), Some(audio));
        assert_eq!(history.directory("output"), Some(root.path().to_path_buf()));
    }

    #[test]
    fn missing_or_corrupt_preferences_fall_back_safely() {
        let root = tempfile::tempdir().unwrap(); let settings = root.path().join("preferences.json");
        std::fs::write(&settings, b"not JSON").unwrap();
        let preferences = Preferences::load(settings);
        assert_eq!(preferences.history.lock().unwrap().directory("audio"), None);
        preferences.remember("movie", &root.path().join("film.mkv"), false).unwrap();
        preferences.remember("audio", &root.path().join("missing/audio.wav"), false).unwrap();
        assert_eq!(preferences.history.lock().unwrap().directory("audio"), Some(root.path().to_path_buf()));
    }
}
