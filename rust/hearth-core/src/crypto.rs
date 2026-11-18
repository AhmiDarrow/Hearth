//! Keys and identities, exactly as Hearth 0.3.x makes them: the group key from the
//! password (PBKDF2-HMAC-SHA256, 210,000 rounds), the peer token, beacon MACs, and the device
//! id (from an Ed25519 key that is used for nothing else).

use std::path::Path;

use base64::Engine;
use hmac::{Hmac, KeyInit, Mac};
use rand::Rng;
use sha2::Sha256;

/// PBKDF2 rounds for the group key.
pub const PBKDF2_ROUNDS: u32 = 210_000;

/// The group key: 32 bytes from the group id, password and salt.
pub fn group_key(group_id: &str, password: &str, salt: &[u8]) -> [u8; 32] {
    let mut out = [0u8; 32];
    pbkdf2::pbkdf2_hmac::<Sha256>(format!("{group_id}:{password}").as_bytes(), salt, PBKDF2_ROUNDS, &mut out);
    out
}

/// HMAC-SHA256 of `msg` under `key`, as 64 lowercase hex characters.
pub fn hmac_hex(key: &[u8], msg: &[u8]) -> String {
    let Ok(mut m) = <Hmac<Sha256> as KeyInit>::new_from_slice(key) else { return String::new() };
    m.update(msg);
    hex::encode(m.finalize().into_bytes())
}

/// The token every peer request carries (`x-hearth-auth`): the group id signed with the key.
pub fn auth_token(key: &[u8], group_id: &str) -> String {
    hmac_hex(key, group_id.as_bytes())
}

/// Compares two strings in time that depends only on their lengths.
pub fn same(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// The first 32 hex characters of the key (stored as the group's verifier, as 0.3.x does).
pub fn verifier(key: &[u8]) -> String {
    let mut h = hex::encode(key);
    h.truncate(32);
    h
}

const PASSWORD_ALPHABET: &[u8] = b"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

/// A group password the house can type: three groups of four, like `AbCd-2345-xYz9`.
pub fn generate_password() -> String {
    let mut rng = rand::thread_rng();
    let chunk = |rng: &mut rand::rngs::ThreadRng| -> String {
        (0..4).filter_map(|_| PASSWORD_ALPHABET.get(rng.gen_range(0..PASSWORD_ALPHABET.len())).map(|c| *c as char)).collect()
    };
    [chunk(&mut rng), chunk(&mut rng), chunk(&mut rng)].join("-")
}

/// `n` random bytes as lowercase hex.
pub fn random_hex(n: usize) -> String {
    let mut b = vec![0u8; n];
    rand::thread_rng().fill(b.as_mut_slice());
    hex::encode(b)
}

/// This PC's identity in a group: an Ed25519 seed kept in `device.key`, and the id made from
/// its public key.
#[derive(Clone, Debug)]
pub struct Device {
    /// The id other PCs know this one by (22 characters).
    pub id: String,
}

impl Device {
    /// Loads `device.key` from `dir`, making one if there is none. An unreadable key is kept as
    /// `device.key.corrupt` and replaced, as 0.3.x does.
    pub fn load_or_create(dir: &Path) -> std::io::Result<Device> {
        let path = dir.join("device.key");
        let seed: Option<[u8; 32]> = std::fs::read(&path).ok().and_then(|b| b.try_into().ok());
        let seed = match seed {
            Some(s) => s,
            None => {
                if path.exists() {
                    let _ = std::fs::rename(&path, dir.join("device.key.corrupt"));
                }
                let mut s = [0u8; 32];
                rand::thread_rng().fill(&mut s);
                std::fs::create_dir_all(dir)?;
                std::fs::write(&path, s)?;
                s
            }
        };
        Ok(Device { id: device_id(&seed) })
    }
}

/// The device id for an Ed25519 seed: the public key in unpadded URL-safe base64, first 22
/// characters.
pub fn device_id(seed: &[u8; 32]) -> String {
    let public = ed25519_dalek::SigningKey::from_bytes(seed).verifying_key();
    let mut s = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(public.as_bytes());
    s.truncate(22);
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_are_hmacs_of_the_group_id() {
        // HMAC-SHA256 test vector (RFC 4231 case 2): key "Jefe".
        assert_eq!(
            hmac_hex(b"Jefe", b"what do ya want for nothing?"),
            "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
        );
        let key = group_key("0123456789abcdef", "pw", &[7u8; 16]);
        assert_eq!(auth_token(&key, "g").len(), 64);
        assert_eq!(verifier(&key).len(), 32);
        assert!(same("abc", "abc") && !same("abc", "abd") && !same("abc", "ab"));
    }

    #[test]
    fn passwords_look_like_0_3_ones() {
        let p = generate_password();
        let parts: Vec<&str> = p.split('-').collect();
        assert_eq!(parts.len(), 3);
        assert!(parts.iter().all(|c| c.len() == 4 && c.bytes().all(|b| PASSWORD_ALPHABET.contains(&b))));
    }

    #[test]
    fn device_ids_are_22_url_safe_characters_and_stable() {
        let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
        let a = Device::load_or_create(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        let b = Device::load_or_create(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(a.id, b.id);
        assert_eq!(a.id.len(), 22);
        assert!(a.id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'));
        // A damaged key is set aside and replaced.
        std::fs::write(dir.path().join("device.key"), b"short").unwrap_or_else(|e| unreachable!("{e}"));
        let c = Device::load_or_create(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert_ne!(c.id, a.id);
        assert!(dir.path().join("device.key.corrupt").exists());
    }
}
