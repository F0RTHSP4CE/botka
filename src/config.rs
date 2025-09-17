//! Bot configuration.
//!
//! For documentation on each field, see comments in the `config.example.yaml`
//! file in the repository root. Here its contents:
//!
//! ```yaml
#![doc = include_str!("../config.example.yaml")]
//! ```

use std::net::SocketAddr;

use reqwest::Url;
use serde::{Deserialize, Serialize};
use teloxide::types::{ChatId, ThreadId, UserId};

use crate::utils::ThreadIdPair;

/// The root configuration structure for the bot.
#[derive(Serialize, Deserialize, Debug)]
pub struct Config {
    pub telegram: Telegram,
    pub server_addr: SocketAddr,
    pub services: Services,
    #[serde(default)]
    pub nlp: NlpConfig,
    #[serde(default)]
    pub borrowed_items: BorrowedItemsConfig,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Telegram {
    pub token: String,
    pub admins: Vec<UserId>,
    pub passive_mode: bool,
    pub chats: TelegramChats,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct TelegramChats {
    pub residential: Vec<ChatId>,
    pub borrowed_items: Vec<ThreadIdPair>,
    pub dashboard: ThreadIdPair,
    pub forward_channel: ChatId,
    pub forward_pins: Vec<FowardPins>,
    pub needs: ThreadIdPair,
    pub mac_monitoring: ThreadIdPair,
    pub ask_to_visit: ThreadIdPair,
    pub resident_owned: Vec<ResidentOwned>,
    pub wikijs_updates: ThreadIdPair,
    pub vortex_of_doom: VortexOfDoom,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct ResidentOwned {
    pub id: ChatId,
    pub internal: bool,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct FowardPins {
    pub from: ChatId,
    pub to: ChatId,
    pub ignore_threads: Vec<ThreadId>,
}

/// Every tuesday on 07:00
fn default_vortex_of_doom_schedule() -> String {
    "0 0 7 * * 2 *".to_string()
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct VortexOfDoom {
    #[serde(default = "default_vortex_of_doom_schedule")]
    pub schedule: String,
    pub chat: ThreadIdPair,
    #[serde(default)]
    pub additional_text: Option<String>,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Services {
    pub mikrotik: Mikrotik,
    pub home_assistant: HomeAssistant,
    pub wikijs: WikiJs,
    pub openai: OpenAI,
    #[serde(default)]
    pub ldap: Option<Ldap>,
    pub vortex_of_doom_cam: EspCam,
    pub racovina_cam: EspCam,
    #[serde(default)]
    pub butler: Option<Butler>,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct Mikrotik {
    pub host: String,
    pub username: String,
    pub password: String,
    /// Connection scheme to use for `RouterOS` REST API.
    /// "auto" tries HTTPS first, then falls back to HTTP.
    #[serde(default = "default_mikrotik_scheme")]
    pub scheme: MikrotikScheme,
}

#[derive(Serialize, Deserialize, Debug, Clone, Copy)]
#[serde(rename_all = "lowercase")]
pub enum MikrotikScheme {
    Auto,
    Http,
    Https,
}

#[allow(clippy::unnecessary_wraps)]
const fn default_mikrotik_scheme() -> MikrotikScheme {
    MikrotikScheme::Auto
}

#[derive(Serialize, Deserialize, Debug)]
pub struct HomeAssistant {
    pub host: String,
    pub token: String,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct WikiJs {
    pub url: String,
    pub token: String,
    pub welcome_message_page: String,
    pub dashboard_page: String,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct OpenAI {
    pub api_key: String,
    #[serde(default = "default_openai_api_base")]
    pub api_base: Option<String>,
    /// Used for borrowed items
    #[serde(default = "default_openai_model")]
    pub model: String,
    #[serde(default)]
    pub disable: bool,
}

#[allow(clippy::unnecessary_wraps)]
fn default_openai_api_base() -> Option<String> {
    Some("https://openrouter.ai/api/v1".to_string())
}

fn default_openai_model() -> String {
    "google/gemini-2.5-flash-preview".to_string()
}

pub fn default_ldap_groups_dn() -> String {
    "ou=groups".to_string()
}

pub fn default_ldap_users_dn() -> String {
    "ou=users".to_string()
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Ldap {
    pub domain: String,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub tls: Option<bool>,
    #[serde(default)]
    pub verify_cert: Option<bool>,
    pub user: String,
    pub password: String,
    pub base_dn: String,
    #[serde(default = "default_ldap_groups_dn")]
    pub groups_dn: String,
    #[serde(default = "default_ldap_users_dn")]
    pub users_dn: String,
    pub attributes: LdapAttributes,
}

fn default_ldap_attribute_user_class() -> String {
    "forthspacePerson".to_string()
}

fn default_ldap_attribute_telegram_id() -> String {
    "telegramId".to_string()
}

fn default_ldap_attribute_group_class() -> String {
    "groupOfUniqueNames".to_string()
}

fn default_ldap_attribute_group_member() -> String {
    "uniqueMember".to_string()
}

fn default_ldap_attribute_resident_group() -> String {
    "residents".to_string()
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct LdapAttributes {
    #[serde(default = "default_ldap_attribute_user_class")]
    pub user_class: String,
    #[serde(default = "default_ldap_attribute_telegram_id")]
    pub telegram_id: String,
    #[serde(default = "default_ldap_attribute_group_class")]
    pub group_class: String,
    #[serde(default = "default_ldap_attribute_group_member")]
    pub group_member: String,
    #[serde(default = "default_ldap_attribute_resident_group")]
    pub resident_group: String,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct EspCam {
    pub url: Url,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Butler {
    pub url: String,
    pub token: String,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct BorrowedItemsConfig {
    #[serde(default)]
    pub reminders: Option<BorrowedItemsReminders>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct BorrowedItemsReminders {
    /// How often to check for overdue items (in hours)
    #[serde(default = "default_borrowed_items_check_interval")]
    pub check_interval_hours: u64,
    /// After how many hours to consider an item overdue
    #[serde(default = "default_borrowed_items_overdue_hours")]
    pub overdue_after_hours: u64,
    /// Maximum number of reminders to send for one item
    #[serde(default = "default_borrowed_items_max_reminders")]
    pub max_reminders: u32,
    /// Interval between reminders (in hours)
    #[serde(default = "default_borrowed_items_reminder_interval")]
    pub reminder_interval_hours: u64,
}

const fn default_borrowed_items_check_interval() -> u64 {
    6 // Check every 6 hours
}

const fn default_borrowed_items_overdue_hours() -> u64 {
    24 // Consider overdue after 24 hours
}

const fn default_borrowed_items_max_reminders() -> u32 {
    3 // Send maximum 3 reminders
}

const fn default_borrowed_items_reminder_interval() -> u64 {
    12 // Send reminders every 12 hours
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct NlpConfig {
    #[serde(default)]
    pub trigger_words: Vec<String>,
    #[serde(default)]
    pub enabled: bool,
    /// Used models in order of performance
    /// and cost. The first one is the cheapest
    /// and dummy model.
    #[serde(default = "default_models")]
    pub models: Vec<String>,
    #[serde(default = "default_search_model")]
    pub search_model: String,
    #[serde(default = "default_classification_model")]
    pub classification_model: Option<String>,
    #[serde(default = "default_max_history")]
    pub max_history: u16,
    #[serde(default = "default_memory_limit")]
    pub memory_limit: i64,
    #[serde(default = "default_random_answer_probability")]
    pub random_answer_probability: f64,
}

const fn default_max_history() -> u16 {
    30
}

const fn default_memory_limit() -> i64 {
    24 * 7 // Default to 1 week in hours
}

fn default_models() -> Vec<String> {
    vec![
        "openai/gpt-4.1-nano".to_string(),
        "openai/gpt-4.1-mini".to_string(),
        "openai/gpt-4.1".to_string(),
    ]
}

fn default_search_model() -> String {
    "openai/gpt-4o-mini-search-preview".to_string()
}

#[allow(clippy::unnecessary_wraps)]
fn default_classification_model() -> Option<String> {
    Some("google/gemini-2.0-flash-lite-001".to_string())
}

const fn default_random_answer_probability() -> f64 {
    3.33 // one in 30
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn check_example_config() -> anyhow::Result<()> {
        let config_text = std::fs::read_to_string("config.example.yaml")?;
        let config: Config = serde_yaml::from_str(&config_text)?;

        similar_asserts::assert_serde_eq!(
            serde_yaml::to_value(config)?,
            serde_yaml::from_str::<serde_yaml::Value>(&config_text)?,
            "Extra fields in config.example.yaml?",
        );

        Ok(())
    }
}
