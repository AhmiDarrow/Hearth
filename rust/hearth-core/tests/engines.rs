//! Two engines in one process: joining a nearby group checks the password with the PC that
//! offered it before anything is saved.

use hearth_core::{Hearth, Offer};

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(0)
}

#[test]
fn a_nearby_group_checks_the_password_before_joining() {
    let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
    let host =
        Hearth::start(&dir.path().join("a"), free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    let guest =
        Hearth::start(&dir.path().join("b"), free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    let g = host.create_group("Home", Some("kettle on the stove"));
    let offer = Offer {
        group_id: g.id.clone(),
        group_name: g.name.clone(),
        salt: Some(g.salt.clone()),
        from: "Den".into(),
        host: format!("127.0.0.1:{}", host.port()),
    };
    let check = |pw: &str| guest.block_on(hearth_core::client::check_password(&guest, &offer, pw));
    assert_eq!(
        check("wrong"),
        Ok(false),
        "a wrong password is refused by the host"
    );
    assert_eq!(check("kettle on the stove"), Ok(true));
    assert!(guest.group().is_none(), "checking joins nothing");
    // No salt: too old to join this way.
    let old = Offer {
        salt: None,
        ..offer.clone()
    };
    assert!(
        guest
            .block_on(hearth_core::client::check_password(&guest, &old, "x"))
            .is_err()
    );
    // Nobody there.
    let gone = Offer {
        host: format!("127.0.0.1:{}", free_port()),
        ..offer
    };
    assert!(
        guest
            .block_on(hearth_core::client::check_password(&guest, &gone, "x"))
            .is_err()
    );
}

fn wait(what: &str, f: impl Fn() -> bool) {
    for _ in 0..600 {
        if f() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    unreachable!("timed out waiting for {what}");
}

fn texts(h: &Hearth) -> Vec<String> {
    h.chat()
        .iter()
        .filter_map(|m| m.get("text").and_then(|t| t.as_str()).map(str::to_owned))
        .collect()
}

/// Three PCs in one group, every one knowing the others' addresses.
fn house(dir: &std::path::Path) -> [Hearth; 3] {
    let start =
        |n: &str| Hearth::start(&dir.join(n), free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    let (a, b, c) = (start("a"), start("b"), start("c"));
    let g = a.create_group("Home", Some("kettle on the stove"));
    let invite = hearth_core::invite::make(
        &g.id,
        &g.salt,
        &g.password,
        &g.name,
        &[format!("127.0.0.1:{}", a.port())],
    );
    for (pc, name) in [(&b, "Den"), (&c, "Attic")] {
        pc.set_device_name(name);
        let _ = pc.join_invite(&invite);
    }
    a.set_device_name("Kitchen");
    for (x, y) in [(&a, &b), (&a, &c), (&b, &c)] {
        x.add_host(&format!("127.0.0.1:{}", y.port()));
        y.add_host(&format!("127.0.0.1:{}", x.port()));
    }
    for pc in [&a, &b, &c] {
        wait("everyone online", || {
            pc.members()
                .iter()
                .filter(|m| m.online && !m.is_self)
                .count()
                == 2
        });
    }
    [a, b, c]
}

#[test]
fn private_messages_reach_only_their_pc_and_never_leave_by_the_pull_route() {
    let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
    let [a, b, c] = house(dir.path());
    let b_id = b.device_id();
    assert!(a.send_chat("tea for two", Some(&b_id)).is_ok());
    assert!(a.send_chat("dinner at seven", None).is_ok());
    wait("b to hear both", || {
        let t = texts(&b);
        t.contains(&"tea for two".to_owned()) && t.contains(&"dinner at seven".to_owned())
    });
    wait("c to hear the public one", || {
        texts(&c).contains(&"dinner at seven".to_owned())
    });
    // C pulls from A and B over the next maintenance rounds: still never the private one.
    std::thread::sleep(std::time::Duration::from_secs(6));
    assert!(
        !texts(&c).contains(&"tea for two".to_owned()),
        "{:?}",
        texts(&c)
    );
    assert!(
        texts(&a).contains(&"tea for two".to_owned()),
        "the sender sees it"
    );
    // A private message to this PC itself, or to a PC that is not there, is refused.
    assert!(a.send_chat("x", Some(&a.device_id())).is_err());
    assert!(a.send_chat("x", Some("nobody")).is_err());
}

#[test]
fn listings_and_uploads_follow_the_0_4_rules() {
    let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
    let rt = tokio::runtime::Runtime::new().unwrap_or_else(|e| unreachable!("{e}"));
    let missing = dir.path().join("gone");
    assert_eq!(
        rt.block_on(hearth_core::server::list_dir(missing)),
        Err(hearth_core::server::ListError::Missing)
    );
    let file = dir.path().join("a.txt");
    let _ = std::fs::write(&file, b"x");
    assert_eq!(
        rt.block_on(hearth_core::server::list_dir(file)),
        Err(hearth_core::server::ListError::Missing),
        "a file is not a folder"
    );
    let _ = std::fs::create_dir_all(dir.path().join("B folder"));
    let _ = std::fs::write(dir.path().join(".hidden"), b"x");
    let _ = std::fs::write(dir.path().join("Z.txt"), b"xy");
    let got = rt
        .block_on(hearth_core::server::list_dir(dir.path().to_path_buf()))
        .unwrap_or_default();
    let names: Vec<&str> = got
        .iter()
        .filter_map(|v| v.get("name").and_then(|n| n.as_str()))
        .collect();
    assert_eq!(names, ["B folder", "a.txt", "Z.txt"]);
    for (name, bad) in [
        ("file.txt.", true),
        ("file.txt ", true),
        ("a/b/c. ", true),
        ("ok.txt", false),
        (".", false),
        ("..", false),
        ("x.txt:stream.", false),
    ] {
        assert_eq!(
            hearth_core::server::windows_illegal_name(name),
            bad,
            "{name:?}"
        );
    }
}
