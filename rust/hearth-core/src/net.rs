//! This PC's home-network addresses, and where to look for other PCs when broadcasts do not
//! cross a second router (as Hearth 0.3.x looks).

use std::collections::BTreeSet;
use std::net::Ipv4Addr;

/// The TCP port PCs talk on.
pub const PEER_PORT: u16 = 7474;
/// The UDP port beacons go to.
pub const BEACON_PORT: u16 = 45454;
/// The multicast group beacons also go to.
pub const MULTICAST: Ipv4Addr = Ipv4Addr::new(239, 54, 54, 54);

/// Home subnets worth a probe when nothing else is found.
const COMMON_HOME_NETS: [[u8; 3]; 13] = [
    [192, 168, 0],
    [192, 168, 1],
    [192, 168, 2],
    [192, 168, 8],
    [192, 168, 10],
    [192, 168, 50],
    [192, 168, 68],
    [192, 168, 86],
    [10, 0, 0],
    [10, 0, 1],
    [10, 1, 1],
    [172, 16, 0],
    [172, 16, 1],
];

/// Adapters that are virtual (containers, VMs, tunnels): their addresses do not reach the
/// rest of the house.
fn virtual_adapter(name: &str) -> bool {
    const SKIP: [&str; 22] = [
        "docker",
        "veth",
        "vethernet",
        "vbox",
        "vmware",
        "virtualbox",
        "hyper-v",
        "wsl",
        "loopback",
        "bluetooth",
        "virbr",
        "default switch",
        "hyperv",
        "br-",
        "cni",
        "flannel",
        "calico",
        "tun",
        "tap",
        "wsl2",
        "npcap",
        "pseudo-interface",
    ];
    let n = name.to_lowercase();
    SKIP.iter().any(|s| n.contains(s))
}

/// A private, usable IPv4 (RFC 1918; not loopback, link-local, multicast or unspecified).
pub fn usable(ip: Ipv4Addr) -> bool {
    ip.is_private()
        && !ip.is_loopback()
        && !ip.is_link_local()
        && !ip.is_multicast()
        && !ip.is_unspecified()
}

/// This PC's home-network IPv4 addresses, real adapters preferred over virtual ones, sorted
/// as text (as 0.3.x sorts them).
pub fn local_ipv4s() -> Vec<String> {
    let adapters: Vec<(String, Ipv4Addr)> = if_addrs::get_if_addrs()
        .unwrap_or_default()
        .into_iter()
        .filter_map(|i| match i.ip() {
            std::net::IpAddr::V4(v4) if usable(v4) => Some((i.name.clone(), v4)),
            _ => None,
        })
        .collect();
    let real: Vec<&(String, Ipv4Addr)> = adapters
        .iter()
        .filter(|(n, _)| !virtual_adapter(n))
        .collect();
    let chosen: Vec<&(String, Ipv4Addr)> = if real.is_empty() {
        adapters.iter().collect()
    } else {
        real
    };
    let mut found: BTreeSet<String> = chosen.into_iter().map(|(_, ip)| ip.to_string()).collect();
    if found.is_empty() {
        // The route the PC would use to reach the internet.
        if let Ok(s) = std::net::UdpSocket::bind("0.0.0.0:0")
            && s.connect("1.1.1.1:80").is_ok()
            && let Ok(std::net::SocketAddr::V4(a)) = s.local_addr()
            && usable(*a.ip())
        {
            found.insert(a.ip().to_string());
        }
    }
    found.into_iter().collect()
}

/// Addresses worth a direct beacon when broadcasts may not cross a sub-router.
pub fn probe_targets(local: &[String], manual_hosts: &[String]) -> Vec<String> {
    let mine: BTreeSet<&str> = local.iter().map(String::as_str).collect();
    let mut t = BTreeSet::new();
    for ip in local.iter().filter_map(|s| s.parse::<Ipv4Addr>().ok()) {
        let o = ip.octets();
        for h in [1u8, 2, 100, 101, 254] {
            t.insert(Ipv4Addr::new(o[0], o[1], o[2], h).to_string());
        }
    }
    for [a, b, c] in COMMON_HOME_NETS {
        t.insert(Ipv4Addr::new(a, b, c, 1).to_string());
        t.insert(Ipv4Addr::new(a, b, c, 254).to_string());
    }
    for h in manual_hosts
        .iter()
        .map(|h| h.trim())
        .filter(|h| !h.is_empty())
    {
        t.insert(
            h.split(':')
                .next()
                .unwrap_or(h)
                .trim_matches(['[', ']'])
                .to_owned(),
        );
    }
    t.into_iter()
        .filter(|x| !mine.contains(x.as_str()))
        .collect()
}

/// Where every beacon goes: broadcast, multicast, each subnet's broadcast, and the probes.
pub fn beacon_targets(local: &[String], manual_hosts: &[String]) -> Vec<String> {
    let mut t: BTreeSet<String> = BTreeSet::new();
    t.insert("255.255.255.255".into());
    t.insert(MULTICAST.to_string());
    for ip in local.iter().filter_map(|s| s.parse::<Ipv4Addr>().ok()) {
        let o = ip.octets();
        t.insert(Ipv4Addr::new(o[0], o[1], o[2], 255).to_string());
    }
    t.extend(probe_targets(local, manual_hosts));
    t.into_iter().collect()
}

/// A host typed by the user (`ip`, `name`, `ip:port`, `[v6]:port`) as host and port.
pub fn parse_host(raw: &str) -> Option<(String, u16)> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    if let Some(rest) = s.strip_prefix('[') {
        let (host, after) = rest.split_once(']')?;
        let port = after
            .strip_prefix(':')
            .and_then(|p| p.parse().ok())
            .unwrap_or(PEER_PORT);
        return Some((host.to_owned(), port));
    }
    match s.matches(':').count() {
        1 => {
            let (h, p) = s.split_once(':')?;
            Some((h.to_owned(), p.parse().unwrap_or(PEER_PORT)))
        }
        _ => Some((s.to_owned(), PEER_PORT)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn targets_and_hosts() {
        let local = vec!["192.168.1.20".to_owned()];
        let t = beacon_targets(&local, &["10.9.9.9:7474".into()]);
        for want in [
            "255.255.255.255",
            "239.54.54.54",
            "192.168.1.255",
            "192.168.1.1",
            "192.168.1.254",
            "10.0.0.1",
            "10.9.9.9",
        ] {
            assert!(t.iter().any(|x| x == want), "{want} in {t:?}");
        }
        assert!(!t.iter().any(|x| x == "192.168.1.20"), "not ourselves");
        assert_eq!(
            parse_host("192.168.1.8:7475"),
            Some(("192.168.1.8".into(), 7475))
        );
        assert_eq!(parse_host("den-pc"), Some(("den-pc".into(), 7474)));
        assert_eq!(parse_host("[fe80::1]:9000"), Some(("fe80::1".into(), 9000)));
        assert_eq!(parse_host("fe80::1"), Some(("fe80::1".into(), 7474)));
        assert_eq!(parse_host(" "), None);
        assert!(virtual_adapter("vEthernet (WSL)") && !virtual_adapter("Ethernet"));
        assert!(
            usable("192.168.1.2".parse().unwrap_or(Ipv4Addr::LOCALHOST))
                && !usable(Ipv4Addr::LOCALHOST)
        );
    }
}
