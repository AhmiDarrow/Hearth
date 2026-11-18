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
