//! Various commands that do not belong to any other module.

use std::collections::HashMap;
use std::fmt::Write as _;
use std::io::Write as _;
use std::path::Path;
use std::process::Command;
use std::sync::Arc;

use anyhow::Result;
use diesel::prelude::*;
use itertools::Itertools;
use macro_rules_attribute::derive;
use teloxide::prelude::*;
use teloxide::types::{InputFile, ThreadId};
use teloxide::utils::command::BotCommands;
use teloxide::utils::html;
use tokio::sync::RwLock;

use super::mac_monitoring::State;
use crate::common::{
    filter_command, format_users, BotCommandsExt, BotCommandsExtTrait, BotEnv,
    TopicEmojis, UpdateHandler,
};
use crate::db::{DbChatId, DbUserId};
use crate::utils::{write_message_link, BotExt};
use crate::utils::mikrotik::get_leases;
use crate::{models, schema};

#[derive(Clone, BotCommands, BotCommandsExt!)]
#[command(rename_rule = "snake_case")]
pub enum Commands {
    #[command(description = "display this text.")]
    Help,

    #[command(description = "list residents.")]
    #[custom(resident = true)]
    Residents,

    #[command(description = "show residents admin table.")]
    #[custom(resident = true)]
    ResidentsAdminTable,

    #[command(description = "show residents timeline.")]
    #[custom(resident = true)]
    ResidentsTimeline,

    #[command(description = "show status.")]
    Status,

    #[command(description = "show topic list.")]
    #[custom(in_group = false)]
    Topics,

    #[command(description = "show bot version.")]
    Version,
}

pub fn command_handler() -> UpdateHandler {
    filter_command::<Commands>().endpoint(start)
}

async fn start(
    bot: Bot,
    env: Arc<BotEnv>,
    msg: Message,
    mac_monitoring_state: Arc<RwLock<State>>,
    command: Commands,
) -> Result<()> {
    match command {
        Commands::Help => cmd_help(bot, msg).await?,
        Commands::Residents => cmd_list_residents(bot, env, msg).await?,
        Commands::ResidentsAdminTable => {
            cmd_residents_admin_table(bot, env, msg).await?;
        }
        Commands::ResidentsTimeline => {
            cmd_show_residents_timeline(bot, msg).await?;
        }
        Commands::Status => {
            cmd_status(bot, env, msg, mac_monitoring_state).await?;
        }
        Commands::Version => {
            bot.reply_message(&msg, crate::version()).await?;
        }
        Commands::Topics => cmd_topics(bot, env, msg).await?,
    }
    Ok(())
}

async fn cmd_help(bot: Bot, msg: Message) -> Result<()> {
    let mut text = String::new();
    text.push_str("Available commands:\n\n");
    text.push_str(&commands_help::<crate::modules::basic::Commands>());
    text.push_str(&commands_help::<crate::modules::needs::Commands>());
    text.push_str(&commands_help::<crate::modules::userctl::Commands>());
    text.push_str(&commands_help::<crate::modules::camera::Commands>());
    text.push_str(&commands_help::<crate::modules::ldap::Commands>());
    text.push_str(&commands_help::<crate::modules::butler::Commands>());
    text.push_str("\nCommands marked with * are available only to residents.");
    // "..., and with ** are available only to bot technicians."
    bot.reply_message(&msg, text)
        .parse_mode(teloxide::types::ParseMode::Html)
        .await?;
    Ok(())
}

fn commands_help<T: BotCommands + BotCommandsExtTrait>() -> String {
    let descriptions = T::descriptions().to_string();
    let global_description =
        descriptions.find("\n\n/").map(|i| &descriptions[..i]);

    let mut result = String::new();
    if let Some(global_description) = global_description {
        result.push_str(global_description);
        result.push('\n');
    }
    for (cmd, rules) in std::iter::zip(&T::bot_commands(), T::COMMAND_RULES) {
        result.push_str(&cmd.command);
        result.push_str(match (rules.admin, rules.resident) {
            (true, _) => "**",
            (false, true) => "*",
            (false, false) => "",
        });
        result.push_str(
            match (rules.in_private, rules.in_group, rules.in_resident_chat) {
                (true, true, _) => "",
                (true, false, _) => " (in private)",
                (false, true, false) => " (not in private)",
                (_, _, true) => " (in resident chat)",
                (false, false, _) => " (disabled?)",
            },
        );
        result.push_str(" — ");
        result.push_str(&cmd.description);
        result.push('\n');
    }

    result
}

async fn cmd_list_residents(
    bot: Bot,
    env: Arc<BotEnv>,
    msg: Message,
) -> Result<()> {
    let residents: Vec<(DbUserId, Option<models::TgUser>)> =
        schema::residents::table
            .filter(schema::residents::end_date.is_null())
            .left_join(
                schema::tg_users::table
                    .on(schema::residents::tg_id.eq(schema::tg_users::id)),
            )
            .select((
                schema::residents::tg_id,
                schema::tg_users::all_columns.nullable(),
            ))
            .order(schema::residents::begin_date.desc())
            .load(&mut *env.conn())?;
    let mut text = String::new();

    text.push_str("Residents: ");
    format_users(&mut text, residents.iter().map(|(r, u)| (*r, u)));
    text.push('.');
    bot.reply_message(&msg, text)
        .parse_mode(teloxide::types::ParseMode::Html)
        .disable_web_page_preview(true)
        .await?;
    Ok(())
}

async fn cmd_residents_admin_table(
    bot: Bot,
    env: Arc<BotEnv>,
    msg: Message,
) -> Result<()> {
    let script_dev_path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("residents-admin-table.py");
    let script_path = if script_dev_path.exists() {
        script_dev_path.as_os_str()
    } else {
        "f0-residents-admin-table".as_ref()
    };

    bot.send_chat_action(msg.chat.id, teloxide::types::ChatAction::Typing)
        .await?;

    let table = Command::new(script_path).arg(&env.config_path).output()?;
    if !table.status.success() {
        bot.reply_message(&msg, "Failed to generate table.").await?;
        log::error!(
            "Failed to generate table: {}",
            String::from_utf8_lossy(&table.stderr)
        );
        return Ok(());
    }
    bot.reply_message(&msg, String::from_utf8_lossy(&table.stdout))
        .parse_mode(teloxide::types::ParseMode::Html)
        .disable_web_page_preview(true)
        .await?;
    Ok(())
}

async fn cmd_show_residents_timeline(bot: Bot, msg: Message) -> Result<()> {
    let svg = Command::new("f0-residents-timeline")
        .arg("-sqlite")
        .arg(crate::DB_FILENAME)
        .output()?;
    if !svg.status.success() || !svg.stdout.starts_with(b"<svg") {
        bot.reply_message(&msg, "Failed to generate timeline (svg).").await?;
        return Ok(());
    }
    let mut png = Command::new("convert")
        .arg("svg:-")
        .arg("png:-")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()?;
    png.stdin.take().unwrap().write_all(&svg.stdout)?;
    let png = png.wait_with_output()?;
    if !png.status.success() || !png.stdout.starts_with(b"\x89PNG") {
        bot.reply_message(&msg, "Failed to generate timeline (png).").await?;
        return Ok(());
    }
    bot.reply_photo(&msg, InputFile::memory(png.stdout)).await?;
    Ok(())
}

pub async fn cmd_status_text(
    env: &Arc<BotEnv>,
    state: &Arc<RwLock<State>>,
) -> Result<String> {
    let mut text = String::new();

    if let Some(active_users) = (*state.read().await).active_users() {
        let data: Vec<models::TgUser> = schema::tg_users::table
            .filter(
                schema::tg_users::id
                    .eq_any(active_users.iter().map(|id| DbUserId::from(*id))),
            )
            .select(schema::tg_users::all_columns)
            .load(&mut *env.conn())?;

        writeln!(&mut text, "Currently in space: ").unwrap();
        format_users(&mut text, data.iter().map(|u| (u.id, u)));
    } else {
        writeln!(
            &mut text,
            "No data collected yet. Probably Mikrotik password is incorrect. Tell that to the admin."
        )
        .unwrap();
    }

    Ok(text)
}

async fn cmd_status(
    bot: Bot,
    env: Arc<BotEnv>,
    msg: Message,
    state: Arc<RwLock<State>>,
) -> Result<()> {
    // Log on-demand debug info and trigger an immediate Mikrotik check in background
    {
        let who = msg
            .from
            .as_ref()
            .map(|u| format!("{}:{}", u.id.0, u.username.clone().unwrap_or_default()))
            .unwrap_or_else(|| "unknown".to_string());
        let chat = msg.chat.id.0;
        let active_count = (*state.read().await)
            .active_users()
            .map(|s| s.len())
            .unwrap_or(0);
        log::info!(
            "/status requested by user={who} chat={chat} active_users={active_count}"
        );
    }

    {
        let env = Arc::clone(&env);
        tokio::spawn(async move {
            log::debug!("/status: triggering immediate Mikrotik leases fetch");
            match get_leases(&env.reqwest_client, &env.config.services.mikrotik)
                .await
            {
                Ok(leases) => {
                    log::info!(
                        "/status: Mikrotik fetch ok: leases_count={}",
                        leases.len()
                    );
                }
                Err(e) => {
                    log::error!("/status: Mikrotik fetch failed: {e}");
                }
            }
        });
    }

    let text = cmd_status_text(&env, &state).await?;

    bot.reply_message(&msg, text)
        .parse_mode(teloxide::types::ParseMode::Html)
        .disable_web_page_preview(true)
        .await?;

    Ok(())
}

async fn cmd_topics(bot: Bot, env: Arc<BotEnv>, msg: Message) -> Result<()> {
    let Some(user) = &msg.from else { return Ok(()) };

    let user_chats = schema::tg_users_in_chats::table
        .filter(schema::tg_users_in_chats::user_id.eq(DbUserId::from(user.id)))
        .select(schema::tg_users_in_chats::chat_id)
        .load::<DbChatId>(&mut *env.conn())?;

    if user_chats.is_empty() {
        bot.reply_message(&msg, "You are not in any tracked chats.").await?;
        return Ok(());
    }

    let topics: Vec<models::TgChatTopic> = schema::tg_chat_topics::table
        .filter(schema::tg_chat_topics::chat_id.eq_any(user_chats))
        .select(schema::tg_chat_topics::all_columns)
        .load(&mut *env.conn())?;

    if topics.is_empty() {
        bot.reply_message(&msg, "No topics in your chats.").await?;
        return Ok(());
    }

    let topic_emojis = TopicEmojis::fetch(&bot, topics.iter()).await?;

    let mut chats = HashMap::new();
    for topic in &topics {
        chats.entry(topic.chat_id).or_insert_with(Vec::new).push(topic);
    }

    let mut text = String::new();
    for (chat_id, topics) in chats {
        let chat: models::TgChat = schema::tg_chats::table
            .filter(schema::tg_chats::id.eq(chat_id))
            .first(&mut *env.conn())?;
        writeln!(
            &mut text,
            "<b>{}</b>",
            chat.title.as_ref().map_or(String::new(), |t| html::escape(t))
        )
        .unwrap();

        for topic in topics {
            render_topic_link(&mut text, &topic_emojis, topic);
        }
        text.push('\n');
    }

    for lines in text.lines().collect_vec().chunks(100) {
        let text = lines.join("\n");
        bot.reply_message(&msg, text)
            .parse_mode(teloxide::types::ParseMode::Html)
            .disable_web_page_preview(true)
            .await?;
    }

    Ok(())
}

fn render_topic_link(
    out: &mut String,
    emojis: &TopicEmojis,
    topic: &models::TgChatTopic,
) {
    write_message_link(out, topic.chat_id, ThreadId::from(topic.topic_id).0);
    out.push_str(emojis.get(topic));
    out.push(' ');
    if let Some(name) = &topic.name {
        out.push_str(&html::escape(name));
    } else {
        write!(out, "Topic #{}", ThreadId::from(topic.topic_id)).unwrap();
    }
    out.push_str("</a>\n");
}
