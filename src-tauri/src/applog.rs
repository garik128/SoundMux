//! Service log, the Rust half. The Python worker writes the same files (backend/applog.py): one short line per
//! event, UTC time, `soundmux.log` rotated at MAX_BYTES into `soundmux.1.log` and `soundmux.2.log`.
//! Logging never fails a job: every error in here is ignored.
use std::{fs::OpenOptions, io::Write, path::{Path, PathBuf}, time::{SystemTime, UNIX_EPOCH}};

pub const MAX_BYTES: u64 = 256 * 1024;
pub const KEEP: usize = 3;
const NAME: &str = "soundmux";

pub fn file(dir: &Path) -> PathBuf { dir.join(format!("{NAME}.log")) }

/// `2026-10-19T08:50:02Z` from a Unix time (civil-from-days, so no date crate is needed).
pub fn stamp(seconds: u64) -> String {
    let days = (seconds / 86_400) as i64;
    let rest = seconds % 86_400;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z", rest / 3_600, rest % 3_600 / 60, rest % 60)
}

/// The profile folder is replaced, so a log can be shared without the account name in it.
pub fn redact(text: &str) -> String {
    match std::env::var("USERPROFILE") {
        Ok(home) if home.len() > 3 => text.replace(&home, "%USERPROFILE%").replace(&home.replace('\\', "/"), "%USERPROFILE%"),
        _ => text.to_string(),
    }
}

fn numbered(dir: &Path, number: usize) -> PathBuf { dir.join(format!("{NAME}.{number}.log")) }

/// Shift the numbered files by one when the current file is full.
pub fn rotate(dir: &Path, max_bytes: u64, keep: usize) {
    let current = file(dir);
    if std::fs::metadata(&current).map(|m| m.len() < max_bytes).unwrap_or(true) { return; }
    let _ = std::fs::remove_file(numbered(dir, keep - 1));
    for number in (1..keep - 1).rev() { let _ = std::fs::rename(numbered(dir, number), numbered(dir, number + 1)); }
    let _ = std::fs::rename(&current, numbered(dir, 1));
}

fn write_with(dir: &Path, level: &str, text: &str, detail: &str, max_bytes: u64) {
    let _ = std::fs::create_dir_all(dir);
    rotate(dir, max_bytes, KEEP);
    let stamp = stamp(SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0));
    let mut lines = vec![format!("{stamp} [{level}] {text}")];
    let shown: String = if detail.chars().count() > 4000 { format!("...{}", detail.chars().rev().take(4000).collect::<Vec<_>>().into_iter().rev().collect::<String>()) } else { detail.to_string() };
    lines.extend(shown.lines().map(|line| format!("    {line}")));
    if let Ok(mut stream) = OpenOptions::new().create(true).append(true).open(file(dir)) {
        let _ = stream.write_all(format!("{}\n", redact(&lines.join("\n"))).as_bytes());
    }
}

pub fn write(dir: &Path, level: &str, text: &str, detail: &str) { write_with(dir, level, text, detail, MAX_BYTES); }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_time_is_written_as_utc_date() {
        assert_eq!(stamp(0), "1970-01-01T00:00:00Z");
        assert_eq!(stamp(1_792_399_802), "2026-10-19T08:50:02Z");
        assert_eq!(stamp(951_782_400 + 86_399), "2000-02-29T23:59:59Z");
    }

    #[test]
    fn log_stays_within_three_files_whatever_is_written() {
        let dir = tempfile::tempdir().unwrap();
        for index in 0..400 { write_with(dir.path(), "INFO", &format!("событие {index} {}", "x".repeat(300)), "", 8 * 1024); }
        let mut names: Vec<String> = std::fs::read_dir(dir.path()).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        assert_eq!(names, ["soundmux.1.log", "soundmux.2.log", "soundmux.log"]);
        for name in &names { assert!(std::fs::metadata(dir.path().join(name)).unwrap().len() < 8 * 1024 + 1_000, "{name} is too big"); }
        let newest = std::fs::read_to_string(file(dir.path())).unwrap();
        assert!(newest.contains("событие 399"), "the latest event must be in the current file");
    }

    #[test]
    fn long_detail_is_cut_and_indented() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "ERROR", "сбой", &format!("{}\nконец", "a".repeat(10_000)));
        let text = std::fs::read_to_string(file(dir.path())).unwrap();
        assert!(text.len() < 5_000, "{}", text.len());
        assert!(text.contains("    конец"));
    }
}
