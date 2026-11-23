//! JSON written exactly as Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"),
//! ensure_ascii=True)` writes it. Beacon MACs are computed over these bytes, so Rust and
//! Python peers must produce the same text for the same value.

use serde_json::Value;

/// `value` as Python's sorted, compact, ASCII-only JSON.
pub fn dumps_sorted(value: &Value) -> String {
    let mut out = String::new();
    write_with(&mut out, value, true);
    out
}

/// `value` as Python's `json.dumps(obj, separators=(",", ":"))`: compact, ASCII only, keys
/// in their order.
pub fn dumps_compact(value: &Value) -> String {
    let mut out = String::new();
    write_with(&mut out, value, false);
    out
}

fn write(out: &mut String, v: &Value) {
    write_with(out, v, true);
}

/// `value` as Python's `json.dumps(obj, indent=2)` writes it (keys in their order, ASCII only).
pub fn dumps_indent(value: &Value) -> String {
    let mut out = String::new();
    pretty(&mut out, value, 0);
    out
}

fn pretty(out: &mut String, v: &Value, level: usize) {
    let pad = |out: &mut String, n: usize| out.push_str(&"  ".repeat(n));
    match v {
        Value::Array(items) if !items.is_empty() => {
            out.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                pad(out, level + 1);
                pretty(out, item, level + 1);
            }
            out.push('\n');
            pad(out, level);
            out.push(']');
        }
        Value::Object(map) if !map.is_empty() => {
            out.push_str("{\n");
            for (i, (k, val)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                pad(out, level + 1);
                string(out, k);
                out.push_str(": ");
                pretty(out, val, level + 1);
            }
            out.push('\n');
            pad(out, level);
            out.push('}');
        }
        other => write(out, other),
    }
}

fn write_with(out: &mut String, v: &Value, sort: bool) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                out.push_str(&i.to_string());
            } else if let Some(u) = n.as_u64() {
                out.push_str(&u.to_string());
            } else if let Some(f) = n.as_f64() {
                out.push_str(&py_float(f));
            }
        }
        Value::String(s) => string(out, s),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_with(out, item, sort);
            }
            out.push(']');
        }
        Value::Object(map) => {
            // Python sorts by the keys' code points; Rust sorts `str` by bytes (UTF-8), which
            // orders code points the same way.
            let mut keys: Vec<&String> = map.keys().collect();
            if sort {
                keys.sort();
            }
            out.push('{');
            for (i, k) in keys.into_iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                string(out, k);
                out.push(':');
                if let Some(v) = map.get(k) {
                    write_with(out, v, sort);
                }
            }
            out.push('}');
        }
    }
}

fn string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e && (c as u32) != 0x7f => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// A float as Python's `repr` writes it: the shortest digits that read back the same, with an
/// exponent below 1e-4 and from 1e16 up.
fn py_float(f: f64) -> String {
    if f.is_nan() {
        return "NaN".into();
    }
    if f.is_infinite() {
        return if f > 0.0 {
            "Infinity".into()
        } else {
            "-Infinity".into()
        };
    }
    if f == 0.0 {
        return if f.is_sign_negative() {
            "-0.0".into()
        } else {
            "0.0".into()
        };
    }
    // Rust's `{:e}` gives the shortest round-trip digits: "1.2345e16", "-5e-7".
    let e = format!("{f:e}");
    let (mant, exp) = e.split_once('e').unwrap_or((e.as_str(), "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let neg = mant.starts_with('-');
    let digits: String = mant.chars().filter(char::is_ascii_digit).collect();
    let sign = if neg { "-" } else { "" };
    if !(-4..16).contains(&exp) {
        let (head, tail) = digits.split_at(1.min(digits.len()));
        let frac = if tail.is_empty() {
            String::new()
        } else {
            format!(".{tail}")
        };
        let esign = if exp < 0 { '-' } else { '+' };
        return format!("{sign}{head}{frac}e{esign}{:02}", exp.abs());
    }
    let point = exp + 1; // digits before the decimal point
    if point <= 0 {
        format!(
            "{sign}0.{}{digits}",
            "0".repeat(usize::try_from(-point).unwrap_or(0))
        )
    } else {
        let p = usize::try_from(point).unwrap_or(0);
        if digits.len() <= p {
            format!("{sign}{digits}{}.0", "0".repeat(p - digits.len()))
        } else {
            let (a, b) = digits.split_at(p);
            format!("{sign}{a}.{b}")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python_json_dumps() {
        // Values checked against CPython 3.13: json.dumps(x, sort_keys=True,
        // separators=(",", ":"), ensure_ascii=True).
        let v = json!({"v": "HEARTH1", "name": "Bjørn's PC 🔥", "port": 7474, "ips": ["192.168.1.2"], "bridge": false, "ts": 1700000000, "group_id": null});
        assert_eq!(
            dumps_sorted(&v),
            concat!(
                r#"{"bridge":false,"group_id":null,"ips":["192.168.1.2"],"name":"Bj"#,
                "\\",
                "u00f8rn's PC ",
                "\\",
                "ud83d",
                "\\",
                r#"udd25","port":7474,"ts":1700000000,"v":"HEARTH1"}"#
            )
        );
        // DEL is ASCII (kept); other control characters are escaped.
        assert_eq!(
            dumps_sorted(&json!("tab\tquote\"back\\\u{7f}\u{1}")),
            "\"tab\\tquote\\\"back\\\\\u{7f}\\u0001\""
        );
        assert_eq!(
            dumps_indent(&json!({"b": [1, {}], "a": "é"})),
            "{\n  \"b\": [\n    1,\n    {}\n  ],\n  \"a\": \"\\u00e9\"\n}"
        );
        assert_eq!(py_float(1.0), "1.0");
        assert_eq!(py_float(0.1), "0.1");
        assert_eq!(py_float(1e16), "1e+16");
        assert_eq!(py_float(1.5e-5), "1.5e-05");
        assert_eq!(py_float(1234.5678), "1234.5678");
        assert_eq!(py_float(0.0001), "0.0001");
        assert_eq!(py_float(1_700_000_000.25), "1700000000.25");
    }
}
