//! Engine calls the interface makes that involve other PCs: chat, and browsing shares (this
//! PC's own are read straight from disk).

use serde_json::{Value, json};

use crate::client::{Entry, RemoteShare};
use crate::engine::{Event, Hearth, now};

impl Hearth {
    /// Says something to the house, or privately to one PC (`to`, its device id): stored
    /// here and sent to the PCs online now. Others fetch public messages within seconds; a
    /// private one goes only to its PC, while it is online.
    pub fn send_chat(&self, text: &str, to: Option<&str>) -> Result<(), String> {
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
        let to = to.filter(|d| !d.is_empty());
        if let Some(d) = to {
            if d == self.device_id() {
                return Err("Choose another PC".into());
            }
            if !self.members().iter().any(|m| m.device_id == d && m.online) {
                return Err("That PC is offline".into());
            }
        }
        let msg = json!({"id": uuid::Uuid::new_v4().simple().to_string(), "from_id": self.device_id(), "from_name": self.settings().device_name, "to": to, "text": t, "ts": now()});
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
            let items = crate::server::list_dir(dir)
                .await
                .map_err(|e| e.message().to_owned())?;
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
