//! Use English until the authenticated Codex renderer reports its language.
use std::sync::atomic::{AtomicBool, Ordering};

fn is_chinese(language: &str) -> bool {
    let normalized = language.trim().replace('_', "-").to_lowercase();
    matches!(normalized.as_str(), "zh" | "zh-cn" | "zh-sg" | "zh-hans")
        || normalized.starts_with("zh-hans-")
}

static CHINESE: AtomicBool = AtomicBool::new(false);

pub fn set_language(language: &str) {
    CHINESE.store(is_chinese(language), Ordering::Relaxed);
}

pub fn text(chinese: &'static str, english: &'static str) -> &'static str {
    if CHINESE.load(Ordering::Relaxed) { chinese } else { english }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn language_variants_and_fallback() {
        for language in ["zh", "zh-CN", "zh-Hans-CN", " ZH_cn "] {
            assert!(is_chinese(language));
        }
        for language in ["en", "en-US", "fr-FR", "", "zho", "zh-TW", "zh-Hant-TW"] {
            assert!(!is_chinese(language));
        }
    }
}
