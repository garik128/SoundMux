use serde::{Deserialize, Serialize};
use std::{path::{Path, PathBuf}, sync::Mutex};

/// Subfolder created inside the folder the user picks, so the cache never mixes with their files.
pub const CACHE_FOLDER_NAME: &str = "SoundMux cache";

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Load { #[default] Fast, Moderate, Background }

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default)]
struct Stored {
    cache_dir: Option<PathBuf>,
    load: Load,
}

pub struct Settings {
    path: PathBuf,
    stored: Mutex<Stored>,
}

/// True when files can really be created in `dir` (it is created when missing).
pub fn usable(dir: &Path) -> bool {
    std::fs::create_dir_all(dir).is_ok() && tempfile::Builder::new().prefix(".probe-").tempfile_in(dir).is_ok()
}

/// The folder the user picked becomes the parent of the real cache folder.
pub fn cache_folder_for(picked: &Path) -> PathBuf {
    if picked.file_name().is_some_and(|name| name == CACHE_FOLDER_NAME) { picked.to_path_buf() } else { picked.join(CACHE_FOLDER_NAME) }
}

impl Settings {
    pub fn load(path: PathBuf) -> Self {
        let stored = std::fs::read(&path).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
        Self { path, stored: Mutex::new(stored) }
    }

    pub fn load_mode(&self) -> Load { self.stored.lock().unwrap().load }

    pub fn custom_cache(&self) -> Option<PathBuf> { self.stored.lock().unwrap().cache_dir.clone() }

    /// Chosen folder if it works, else the default next to the program, else the per-user fallback.
    /// The flag is true when a saved custom folder had to be skipped.
    pub fn cache_dir(&self, default: &Path, fallback: &Path) -> (PathBuf, bool) {
        let custom = self.custom_cache();
        if let Some(folder) = &custom { if usable(folder) { return (folder.clone(), false); } }
        let skipped = custom.is_some();
        if usable(default) { return (default.to_path_buf(), skipped); }
        (fallback.to_path_buf(), skipped)
    }

    pub fn set_load(&self, load: Load) -> Result<(), String> {
        self.update(|stored| stored.load = load)
    }

    /// `None` returns to the default location. A picked folder must be an existing, writable absolute path.
    pub fn set_cache_dir(&self, picked: Option<PathBuf>) -> Result<(), String> {
        let folder = match picked {
            None => None,
            Some(path) => {
                if !path.is_absolute() { return Err("Папка должна быть указана полным путём.".into()); }
                if !path.is_dir() { return Err("Такой папки нет. Выберите существующую папку.".into()); }
                let folder = cache_folder_for(&path);
                if !usable(&folder) { return Err("В эту папку нельзя записывать файлы. Выберите другую.".into()); }
                Some(folder)
            }
        };
        self.update(|stored| stored.cache_dir = folder)
    }

    fn update(&self, change: impl FnOnce(&mut Stored)) -> Result<(), String> {
        let mut current = self.stored.lock().unwrap();
        let mut updated = current.clone();
        change(&mut updated);
        let data = serde_json::to_vec_pretty(&updated).map_err(|e| e.to_string())?;
        crate::preferences::write_atomic(&self.path, &data)?;
        *current = updated;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn choices_survive_restart() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("settings.json");
        let picked = root.path().join("Кэш [проверка]");
        std::fs::create_dir(&picked).unwrap();
        let first = Settings::load(file.clone());
        assert_eq!(first.load_mode(), Load::Fast);
        first.set_load(Load::Background).unwrap();
        first.set_cache_dir(Some(picked.clone())).unwrap();
        let second = Settings::load(file);
        assert_eq!(second.load_mode(), Load::Background);
        assert_eq!(second.custom_cache(), Some(picked.join(CACHE_FOLDER_NAME)));
        second.set_cache_dir(None).unwrap();
        assert_eq!(second.custom_cache(), None);
    }

    #[test]
    fn corrupt_or_missing_file_uses_defaults() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("settings.json");
        assert_eq!(Settings::load(file.clone()).load_mode(), Load::Fast);
        std::fs::write(&file, b"{ not json").unwrap();
        let settings = Settings::load(file);
        assert_eq!(settings.load_mode(), Load::Fast);
        assert_eq!(settings.custom_cache(), None);
    }

    #[test]
    fn unusable_custom_folder_falls_back_to_default_then_user_folder() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("settings.json");
        let default = root.path().join("cache");
        let fallback = root.path().join("fallback");
        let settings = Settings::load(file);
        assert_eq!(settings.cache_dir(&default, &fallback), (default.clone(), false));
        let blocker = root.path().join("is-a-file");
        std::fs::write(&blocker, b"x").unwrap();
        // A file where a folder is needed makes both the saved folder and the default unusable.
        settings.update(|s| s.cache_dir = Some(blocker.join("inner"))).unwrap();
        assert_eq!(settings.cache_dir(&default, &fallback), (default.clone(), true));
        assert_eq!(settings.cache_dir(&blocker.join("never"), &fallback), (fallback, true));
    }

    #[test]
    fn invalid_choices_are_rejected_and_nothing_is_saved() {
        let root = tempfile::tempdir().unwrap();
        let settings = Settings::load(root.path().join("settings.json"));
        assert!(settings.set_cache_dir(Some(PathBuf::from("relative"))).is_err());
        assert!(settings.set_cache_dir(Some(root.path().join("missing"))).is_err());
        assert_eq!(settings.custom_cache(), None);
        assert_eq!(cache_folder_for(Path::new("D:\\Видео")), Path::new("D:\\Видео").join(CACHE_FOLDER_NAME));
        assert_eq!(cache_folder_for(&Path::new("D:\\Видео").join(CACHE_FOLDER_NAME)), Path::new("D:\\Видео").join(CACHE_FOLDER_NAME));
    }
}
