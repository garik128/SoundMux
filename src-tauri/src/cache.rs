use std::path::Path;

/// Published cache files are `<64 hex>.mp4` (preview clips), `audio-<64 hex>.<extension>` (corrected sound) and
/// `unpacked-<64 hex>.mka` (the tracks of a TrueHD+AC3 file, which are made again when needed).
/// The extension follows the codec of the corrected sound (flac, ac3, eac3, mka, ...), so any short one counts.
fn owned_entry(name: &str) -> bool {
    let (stem, extension) = match name.split_once('.') { Some(parts) => parts, None => return false };
    let key = if extension == "mp4" { stem }
        else if let Some(key) = stem.strip_prefix("audio-").or_else(|| stem.strip_prefix("unpacked-")) { if (1..=5).contains(&extension.len()) && extension.bytes().all(|b| b.is_ascii_alphanumeric()) { key } else { return false; } }
        else { return false; };
    key.len() == 64 && key.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Delete only published cache files. Never follow links or recurse into folders.
pub fn clear(root: &Path) -> Result<u64, String> {
    let mut removed = 0;
    let mut failures = 0;
    for entry in std::fs::read_dir(root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let kind = entry.file_type().map_err(|e| e.to_string())?;
        if !kind.is_file() || !owned_entry(&entry.file_name().to_string_lossy()) { continue; }
        let size = entry.metadata().map_err(|e| e.to_string())?.len();
        match std::fs::remove_file(entry.path()) { Ok(()) => removed += size, Err(_) => failures += 1 }
    }
    if failures > 0 { return Err(format!("Удалена часть кэша. Не удалось удалить файлов: {failures}. Остановите воспроизведение или закройте другие окна SoundMux и повторите.")); }
    Ok(removed)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn clears_only_owned_published_files() {
        let root = tempfile::tempdir().unwrap();
        let key = "a".repeat(64);
        for name in [format!("{key}.mp4"), format!("audio-{key}.flac"), format!("audio-{key}.eac3"), format!("audio-{key}.ac3"), format!("audio-{key}.mka"), format!("unpacked-{key}.mka")] {
            std::fs::write(root.path().join(name), b"123").unwrap();
        }
        let unrelated = root.path().join("movie.mp4");
        std::fs::write(&unrelated, b"source").unwrap();
        let work = root.path().join("job-existing");
        std::fs::create_dir(&work).unwrap();
        std::fs::write(work.join("temporary"), b"work").unwrap();
        let lookalike = root.path().join(format!("audio-{key}.flac.part"));
        std::fs::write(&lookalike, b"x").unwrap();
        assert_eq!(clear(root.path()).unwrap(), 18);
        assert!(lookalike.exists());
        assert!(unrelated.exists());
        assert!(work.join("temporary").exists());
    }
}
