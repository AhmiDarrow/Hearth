//! Invites: the text one PC gives another to join its home group.
//! `hearth|<group id>|<salt hex>|<password>|<group name>|<ip:port>,<ip:port>…`

/// A parsed invite.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Invite {
    /// The group's id.
    pub group_id: String,
    /// The group's salt, as hex.
    pub salt: String,
    /// The group password.
    pub password: String,
    /// The group's name.
    pub name: String,
    /// Where members were reachable when the invite was made (`ip:port`).
    pub hosts: Vec<String>,
}

/// Why an invite was not accepted (the messages Hearth shows).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InviteError {
    /// Not Hearth's format at all.
    NotAnInvite,
    /// Cut short.
    Incomplete,
}

impl InviteError {
    /// The text shown to the user.
    pub fn message(self) -> &'static str {
        match self {
            InviteError::NotAnInvite => "Not a Hearth invite. Ask a member to copy theirs.",
            InviteError::Incomplete => "Invite code is incomplete",
        }
    }
}

/// The invite for a group reachable at `hosts`.
pub fn make(group_id: &str, salt: &str, password: &str, name: &str, hosts: &[String]) -> String {
    format!("hearth|{group_id}|{salt}|{password}|{name}|{}", hosts.join(","))
}

/// Reads an invite in the current form or either 0.2 form (`hearth:id|…` and `hearth:id:…`).
pub fn parse(text: &str) -> Result<Invite, InviteError> {
    let t = text.trim();
    let hosts = |s: Option<&str>| -> Vec<String> {
        s.map(|h| h.split(',').map(str::trim).filter(|x| !x.is_empty()).map(str::to_owned).collect()).unwrap_or_default()
    };
    let build = |parts: &[&str], first: usize| -> Invite {
        let get = |i: usize| parts.get(i).copied().unwrap_or("").to_owned();
        Invite {
            group_id: get(first),
            salt: get(first + 1),
            password: get(first + 2),
            name: get(first + 3),
            hosts: hosts(parts.get(first + 4).copied()),
        }
    };
    if t.starts_with("hearth|") {
        let parts: Vec<&str> = t.split('|').collect();
        if parts.len() < 5 {
            return Err(InviteError::Incomplete);
        }
        return Ok(build(&parts, 1));
    }
    if let Some(rest) = t.strip_prefix("hearth:") {
        let parts: Vec<&str> = if rest.contains('|') { rest.split('|').collect() } else { rest.splitn(5, ':').collect() };
        if parts.len() < 4 {
            return Err(InviteError::Incomplete);
        }
        return Ok(build(&parts, 0));
    }
    Err(InviteError::NotAnInvite)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invites_round_trip_and_old_forms_parse() {
        let text = make("0123456789abcdef", "00ff", "Ab-Cd", "Home", &["192.168.1.5:7474".into(), "10.0.0.2:7474".into()]);
        assert_eq!(text, "hearth|0123456789abcdef|00ff|Ab-Cd|Home|192.168.1.5:7474,10.0.0.2:7474");
        let i = parse(&format!("  {text}\n")).unwrap_or_else(|e| unreachable!("{e:?}"));
        assert_eq!(i.hosts, vec!["192.168.1.5:7474", "10.0.0.2:7474"]);
        assert_eq!(i.password, "Ab-Cd");
        let pipe = parse("hearth:id|salt|pw|Home|1.2.3.4:7474").unwrap_or_else(|e| unreachable!("{e:?}"));
        let colon = parse("hearth:id:salt:pw:Home:1.2.3.4:7474").unwrap_or_else(|e| unreachable!("{e:?}"));
        assert_eq!(pipe, colon);
        assert_eq!(parse("hearth|a|b|c"), Err(InviteError::Incomplete));
        assert_eq!(parse("hello"), Err(InviteError::NotAnInvite));
        assert_eq!(parse("hearth|a|b|c|Home").map(|i| i.hosts), Ok(vec![]));
    }
}
