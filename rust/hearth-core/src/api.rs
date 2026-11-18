//! Engine calls the interface makes that involve other PCs: chat, and browsing shares (this
//! PC's own are read straight from disk).

use serde_json::{Value, json};

use crate::client::{Entry, RemoteShare};
use crate::engine::{Event, Hearth, now};

impl Hearth {
    /// Says something to the house: stored here, sent to every PC that is online now (the rest
    /// fetch it within seconds).
    pub fn send_chat(&self, text: &str) -> Result<(), String> {
        if self.group().is_none() {
            return Err("Join a hearth first".into());
        }
        let t = text.trim();
        if t.is_empty() {
            return Err("Empty message".into());
        }
        if t.chars().count() > 4000 {
            return Err("Messages can be up to 4000 characters".into());
        }
        let msg = json!({"id": uuid::Uuid::new_v4().simple().to_string(), "from_id": self.device_id(), "from_name": self.settings().device_name, "to": null, "text": t, "ts": now()});
        self.store().append_chat(msg.clone());
        self.save();
        self.emit(Event::Chat);
        let me = self.clone();
        self.spawn(
            async move { crate::client::push_chat(&me, &msg).await },
            |()| {},
        );
        Ok(())
    }

    /// A PC's shares (this PC's own directly).
    pub async fn shares_of(&self, device_id: &str) -> Result<Vec<RemoteShare>, String> {
        if device_id == self.device_id() {
            return Ok(self
                .shares()
                .into_iter()
                .map(|s| RemoteShare {
                    label: s.display_label(),
                    id: s.id,
                    writable: s.writable,
                })
                .collect());
        }
        crate::client::shares(self, device_id).await
    }

    /// A folder in a PC's share (this PC's own directly).
    pub async fn files_of(
        &self,
        device_id: &str,
        share: &str,
        path: &str,
    ) -> Result<Vec<Entry>, String> {
        if device_id == self.device_id() {
            let (_, dir) = crate::server::locate(self, share, path)
                .map_err(|_| "That folder is no longer available.".to_owned())?;
            let items = if dir.is_dir() {
                crate::server::list_dir(&dir)
            } else {
                Vec::new()
            };
            return Ok(crate::client::entries(
                &json!({"items": Value::Array(items)}),
            ));
        }
        crate::client::files(self, device_id, share, path).await
    }

    /// The local path of a file in one of this PC's shares (to drag it out to other apps).
    pub fn local_path(&self, share: &str, path: &str) -> Option<std::path::PathBuf> {
        crate::server::locate(self, share, path)
            .ok()
            .map(|(_, p)| p)
            .filter(|p| p.exists())
    }
}
