//! Private device-channel configuration written by the shell before the
//! sidecar starts. It can only DISABLE channels; it never widens the loopback
//! control boundary, grants permissions, or exposes credentials.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::sidecar::{safe_storage_path, write_private_text_file};

pub const DEVICE_CHANNEL_FILE_NAME: &str = "device_channel.json";
pub const DEVICE_CHANNEL_VERSION: u16 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceChannelConfig {
    pub version: u16,
    /// Master switch for all device endpoints.
    pub enabled: bool,
    /// HTTP relay sessions/messages under /api/v1/remote/*.
    pub relay_enabled: bool,
    /// Device-authenticated WebSocket scope under /device/*.
    pub ws_enabled: bool,
}

impl Default for DeviceChannelConfig {
    fn default() -> Self {
        Self {
            version: DEVICE_CHANNEL_VERSION,
            enabled: true,
            relay_enabled: true,
            ws_enabled: true,
        }
    }
}

impl DeviceChannelConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.version != DEVICE_CHANNEL_VERSION {
            return Err(format!(
                "unsupported device channel config version: {}", self.version
            ));
        }
        Ok(())
    }

    pub fn relay_allowed(&self) -> bool {
        self.enabled && self.relay_enabled
    }

    pub fn ws_allowed(&self) -> bool {
        self.enabled && self.ws_enabled && self.relay_enabled
    }

    /// A disabled/corrupt config is fail-closed, never silently enabled.
    pub fn disabled() -> Self {
        Self {
            version: DEVICE_CHANNEL_VERSION,
            enabled: false,
            relay_enabled: false,
            ws_enabled: false,
        }
    }
}

pub fn config_path(dir: &Path) -> Result<PathBuf, String> {
    let path = dir.join(DEVICE_CHANNEL_FILE_NAME);
    safe_storage_path(&path)?;
    Ok(path)
}

pub fn read_config(dir: &Path) -> Result<DeviceChannelConfig, String> {
    let path = config_path(dir)?;
    if !path.is_file() {
        return Ok(DeviceChannelConfig::default());
    }
    let bytes = std::fs::read(&path)
        .map_err(|e| format!("read device channel config: {e}"))?;
    let config: DeviceChannelConfig = serde_json::from_slice(&bytes)
        .map_err(|e| format!("parse device channel config: {e}"))?;
    config.validate()?;
    Ok(config)
}

pub fn write_config(dir: &Path, config: &DeviceChannelConfig) -> Result<(), String> {
    config.validate()?;
    let content = serde_json::to_vec_pretty(config)
        .map_err(|e| format!("serialize device channel config: {e}"))?;
    write_private_text_file(dir, DEVICE_CHANNEL_FILE_NAME, &content)?;
    Ok(())
}

/// Write a default config on first launch so the shell owns the file.
pub fn ensure_config(dir: &Path) -> Result<DeviceChannelConfig, String> {
    let path = config_path(dir)?;
    if path.is_file() {
        return read_config(dir);
    }
    let config = DeviceChannelConfig::default();
    write_config(dir, &config)?;
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "sythion_device_channel_{}_{}", name, std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn default_config_is_valid_and_enables_both_channels() {
        let config = DeviceChannelConfig::default();
        assert!(config.validate().is_ok());
        assert!(config.relay_allowed());
        assert!(config.ws_allowed());
    }

    #[test]
    fn roundtrip_preserves_all_flags() {
        let dir = temp_dir("roundtrip");
        let config = DeviceChannelConfig {
            version: DEVICE_CHANNEL_VERSION,
            enabled: true,
            relay_enabled: true,
            ws_enabled: false,
        };
        write_config(&dir, &config).unwrap();
        assert_eq!(read_config(&dir).unwrap(), config);
        let disabled = DeviceChannelConfig {
            ws_enabled: false,
            relay_enabled: false,
            ..config
        };
        assert!(!disabled.ws_allowed());
        assert!(!disabled.relay_allowed());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn missing_config_uses_default_and_ensure_writes_it() {
        let dir = temp_dir("missing");
        assert_eq!(read_config(&dir).unwrap(), DeviceChannelConfig::default());
        let written = ensure_config(&dir).unwrap();
        assert_eq!(written, DeviceChannelConfig::default());
        assert!(config_path(&dir).unwrap().is_file());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn invalid_version_is_rejected() {
        let dir = temp_dir("invalid");
        let bad = DeviceChannelConfig {
            version: 99,
            ..DeviceChannelConfig::default()
        };
        assert!(write_config(&dir, &bad).is_err());
        assert!(bad.validate().is_err());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
