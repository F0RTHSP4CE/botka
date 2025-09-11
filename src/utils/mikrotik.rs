use std::time::Duration;

use crate::config::Mikrotik;

#[derive(serde::Deserialize, Debug)]
#[serde(rename_all = "kebab-case")]
pub struct Lease {
    pub mac_address: String,
    #[serde(deserialize_with = "crate::utils::deserealize_duration")]
    pub last_seen: Duration,
}

pub async fn get_leases(
    reqwest_client: &reqwest::Client,
    conf: &Mikrotik,
) -> Result<Vec<Lease>, reqwest::Error> {
    async fn attempt(
        client: &reqwest::Client,
        conf: &Mikrotik,
        scheme: &str,
    ) -> Result<Vec<Lease>, reqwest::Error> {
        // Preserve legacy behavior: POST to /print with .proplist
        let url = format!(
            "{scheme}://{}/rest/ip/dhcp-server/lease/print",
            conf.host
        );
        let request = client
            .post(url)
            .timeout(Duration::from_secs(5))
            .basic_auth(&conf.username, Some(&conf.password))
            .json(&serde_json::json!({
                ".proplist": ["mac-address", "last-seen"],
            }));
        match request.send().await {
            Ok(resp) => {
                let status = resp.status();
                if !status.is_success() {
                    // Convert to reqwest::Error preserving status, then log details
                    let err = resp.error_for_status().unwrap_err();
                    log::error!(
                        "Mikrotik HTTP status error: scheme={scheme} host={} status={} url={}",
                        conf.host,
                        status.as_u16(),
                        err.url().map_or("<none>", |u| u.as_str())
                    );
                    return Err(err);
                }
                resp.json::<Vec<Lease>>().await
            }
            Err(err) => {
                // Network / TLS / timeout errors
                let mut source_chain = String::new();
                let mut cur: &(dyn std::error::Error + 'static) = &err;
                while let Some(src) = cur.source() {
                    use std::fmt::Write as _;
                    let _ = write!(source_chain, " -> {}", src);
                    cur = src;
                }
                log::error!(
                    "Mikrotik request error: scheme={scheme} host={} err={}{}",
                    conf.host,
                    err,
                    source_chain
                );
                Err(err)
            }
        }
    }

    // Try HTTPS first, then fall back to HTTP (some RouterOS setups disable HTTPS)
    let leases_https = attempt(reqwest_client, conf, "https").await;
    if let Err(ref e) = leases_https {
        log::warn!(
            "Mikrotik https request failed, retrying over http: host={} err={}",
            conf.host,
            e
        );
    }
    let leases = match leases_https {
        Ok(v) => Ok(v),
        Err(_e_https) => attempt(reqwest_client, conf, "http").await,
    };

    crate::metrics::update_service("mikrotik", leases.is_ok());
    leases
}
