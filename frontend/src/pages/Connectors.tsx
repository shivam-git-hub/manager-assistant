import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import outlookIcon from "@/assets/outlook_icon.png";
import slackLogo from "@/assets/slack_logo.png";
import teamsLogo from "@/assets/teams_logo.png";
import {
  disconnectOutlook,
  disconnectSlack,
  getConnections,
  goToOutlookEnableSend,
  goToOutlookLogin,
  goToSlackInstall,
  revokeOutlookSend,
  type Connections,
} from "@/lib/api";

// Connectors page (wireframe 10.png): one row per connector, Read/Send
// permission pills, Grant/Revoke actions, Teams parked under "Coming
// Soon". All state comes from GET /api/auth/connections.

function Pill({ enabled }: { enabled: boolean }) {
  return (
    <span
      className={`inline-block w-24 text-center rounded-md px-3 py-1 text-xs font-bold ${
        enabled ? "bg-[#67C05B] text-white" : "bg-[#DD5454] text-white"
      }`}
    >
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}

function ActionButton({
  label,
  onClick,
  disabled,
  title,
}: {
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`w-20 rounded-md px-3 py-1 text-xs font-semibold ${
        disabled
          ? "bg-card text-inksoft cursor-not-allowed"
          : "bg-nav/15 text-nav hover:bg-nav hover:text-white transition-colors"
      }`}
    >
      {label}
    </button>
  );
}

function PermissionRow({
  name,
  pill,
  action,
}: {
  name: "Read" | "Send";
  pill: React.ReactNode;
  action: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[4rem_7rem_6rem] items-center gap-4">
      <span className="text-ink font-medium">{name}</span>
      {pill}
      {action}
    </div>
  );
}

function ConnectorRow({
  icon,
  name,
  subtitle,
  children,
}: {
  icon: string;
  name: string;
  subtitle?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-6 py-6 flex-wrap">
      <div className="flex items-center gap-4 min-w-52">
        <img src={icon} alt="" className="h-11 w-11 object-contain" />
        <div>
          <div className="text-lg font-semibold text-ink">{name}</div>
          {subtitle && <div className="text-xs text-inksoft">{subtitle}</div>}
        </div>
      </div>
      <div className="flex flex-col gap-3">{children}</div>
    </div>
  );
}

export default function Connectors() {
  const [conn, setConn] = useState<Connections | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

  const load = useCallback(() => {
    getConnections().then(setConn).catch(() => setConn(null));
  }, []);
  useEffect(load, [load]);

  // Post-OAuth landing (backend redirects here with ?connected=...).
  useEffect(() => {
    const connected = params.get("connected");
    if (connected === "outlook") setNotice("Outlook permissions updated");
    if (connected === "slack")
      setNotice(`Slack connected${params.get("workspace") ? ` to ${params.get("workspace")}` : ""}`);
    if (connected) setParams({}, { replace: true });
  }, [params, setParams]);

  async function revokeOutlook() {
    await disconnectOutlook();
    setNotice("Outlook disconnected. Pulse no longer reads or sends from this mailbox.");
    load();
  }

  async function revokeOutlookSendOnly() {
    await revokeOutlookSend();
    setNotice(
      "Send access revoked -- Pulse will no longer send mail as you. Reading is unaffected. " +
        "(Microsoft keeps the consent record itself; remove it at account.live.com/consent if you want it gone there too.)",
    );
    load();
  }

  async function revokeSlack() {
    await disconnectSlack();
    setNotice(
      "Slack disconnected: reading AND sending have both stopped -- your agent is offline. " +
        "Your claim on the agent identity is kept, so reinstalling brings back the same bot.",
    );
    load();
  }

  function grantSlack() {
    if (!conn?.slack) {
      navigate("/agents");
      return;
    }
    goToSlackInstall();
  }

  if (!conn) return null;

  const outlook = conn.outlook;
  const slack = conn.slack;

  return (
    <main className="mx-auto max-w-4xl px-6 py-8">
      <h1 className="text-2xl font-extrabold text-ink text-center">Connectors</h1>

      {notice && (
        <div className="mt-4 rounded-md border border-cardline bg-card px-4 py-2 text-sm text-ink flex justify-between items-center">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} aria-label="Dismiss notice" className="text-inksoft hover:text-ink">
            &times;
          </button>
        </div>
      )}

      <div className="mt-4 grid grid-cols-[4rem_7rem_6rem] gap-4 justify-end text-sm font-bold text-ink">
        <span className="col-start-2 text-center">Permissions</span>
        <span className="text-center">Actions</span>
      </div>

      <div className="divide-y divide-cardline">
        <ConnectorRow icon={outlookIcon} name="Outlook" subtitle={outlook.mailbox_email}>
          <PermissionRow
            name="Read"
            pill={<Pill enabled={outlook.connected} />}
            action={
              outlook.connected ? (
                <ActionButton
                  label="Revoke"
                  onClick={revokeOutlook}
                  title="Forgets Pulse's copy of the mailbox tokens (send access goes with it)"
                />
              ) : (
                <ActionButton label="Grant" onClick={goToOutlookLogin} />
              )
            }
          />
          <PermissionRow
            name="Send"
            pill={<Pill enabled={!!outlook.send_enabled} />}
            action={
              outlook.send_enabled ? (
                <ActionButton
                  label="Revoke"
                  onClick={revokeOutlookSendOnly}
                  title="Pulse stops sending mail as you; reading is unaffected"
                />
              ) : (
                <ActionButton
                  label="Grant"
                  onClick={goToOutlookEnableSend}
                  disabled={!outlook.connected}
                  title={outlook.connected ? undefined : "Grant Read first"}
                />
              )
            }
          />
        </ConnectorRow>

        <ConnectorRow
          icon={slackLogo}
          name="Slack"
          subtitle={slack ? `Agent: ${slack.agent_name}` : "Claim an agent first (see Agents)"}
        >
          <PermissionRow
            name="Read"
            pill={<Pill enabled={!!slack?.read_enabled} />}
            action={
              slack?.read_enabled ? (
                <ActionButton
                  label="Revoke"
                  onClick={revokeSlack}
                  title="Disconnects the Slack install (read and send together); your agent claim is kept"
                />
              ) : (
                <ActionButton label="Grant" onClick={grantSlack} />
              )
            }
          />
          <PermissionRow
            name="Send"
            pill={<Pill enabled={!!slack?.installed} />}
            action={
              slack?.installed ? (
                <ActionButton
                  label="Revoke"
                  onClick={revokeSlack}
                  title="Disconnects the Slack install (read and send together); your agent claim is kept"
                />
              ) : (
                <ActionButton label="Grant" onClick={grantSlack} />
              )
            }
          />
        </ConnectorRow>
      </div>

      <section className="mt-8 rounded-lg bg-card/60 border border-cardline px-6 py-5">
        <h2 className="font-bold text-ink">Coming Soon ..</h2>
        <ConnectorRow icon={teamsLogo} name="Teams">
          <PermissionRow name="Read" pill={<Pill enabled={false} />} action={<ActionButton label="Grant" disabled />} />
          <PermissionRow name="Send" pill={<Pill enabled={false} />} action={<ActionButton label="Grant" disabled />} />
        </ConnectorRow>
      </section>
    </main>
  );
}
