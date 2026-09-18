"""Owner-scoped executors; legacy global executors fail closed after migration."""

import json


EXECUTION_ROLES = ("剪辑", "拍摄", "平面", "即梦")


def init_schema(conn):
    conn.executescript("""
        create table if not exists creative_director_teams (
            owner_thread_id text primary key,
            owner_host_id text not null,
            label text not null,
            updated_at text not null
        );
        create table if not exists creative_team_bindings (
            owner_thread_id text not null references creative_director_teams(owner_thread_id),
            role text not null,
            thread_id text not null unique,
            host_id text not null,
            updated_at text not null,
            primary key(owner_thread_id, role)
        );
        create table if not exists retired_dispatch_prepare_cache (
            request_id text primary key,
            response_json text not null,
            retired_at text not null
        );
        create trigger if not exists reject_shared_executor_insert
        before insert on agent_bindings
        when new.role != '编导' and exists(select 1 from creative_director_teams)
        begin select raise(abort, 'Use director_team_register: global executor binding is disabled'); end;
        create trigger if not exists reject_shared_executor_update
        before update on agent_bindings
        when new.role != '编导' and exists(select 1 from creative_director_teams)
        begin select raise(abort, 'Use director_team_register: global executor binding is disabled'); end;
    """)


def enabled(conn):
    return bool(conn.execute("select 1 from creative_director_teams limit 1").fetchone())


def target(conn, project_id, role):
    return conn.execute("""
        select b.thread_id, b.host_id from creative_team_bindings b
        join projects p on p.owner_thread_id = b.owner_thread_id
        join creative_director_teams t on t.owner_thread_id = b.owner_thread_id
        where p.project_id = ? and b.role = ?
          and p.owner_host_id = t.owner_host_id
    """, (project_id, role)).fetchone()


def register(service, conn, owner, label, bindings, owner_host_id):
    from .service import FIXED_ROLES, WorkflowError

    if not all(isinstance(v, str) and v.strip() for v in (owner, label, owner_host_id)):
        raise WorkflowError("owner, label and host are required")
    if set(bindings) != set(EXECUTION_ROLES):
        raise WorkflowError("Each team needs exactly 剪辑, 拍摄, 平面 and 即梦")
    director = conn.execute("""
        select 1 from agent_thread_bindings
        where role = '编导' and thread_id = ? and host_id = ? and status = 'active'
    """, (owner, owner_host_id)).fetchone()
    if not director:
        raise WorkflowError("Team owner must be an active registered director")
    ids = []
    for role, item in bindings.items():
        if not isinstance(item, dict) or not all(
            isinstance(item.get(k), str) and item[k].strip() for k in ("thread_id", "host_id")
        ):
            raise WorkflowError("Each executor needs thread_id and host_id")
        tid = item["thread_id"]
        if tid in ids or conn.execute(
            "select 1 from agent_thread_bindings where role = '编导' and thread_id = ?",
            (tid,),
        ).fetchone():
            raise WorkflowError("A director or another role cannot double as an executor")
        ids.append(tid)
        existing = conn.execute(
            "select owner_thread_id, role from creative_team_bindings where thread_id = ?", (tid,)
        ).fetchone()
        if existing and (existing["owner_thread_id"] != owner or existing["role"] != role):
            raise WorkflowError("Executor already belongs to a different director or role")
    now = service._now()
    conn.execute("""
        insert into creative_director_teams values (?, ?, ?, ?)
        on conflict(owner_thread_id) do update set
        owner_host_id=excluded.owner_host_id, label=excluded.label, updated_at=excluded.updated_at
    """, (owner, owner_host_id, label, now))

    # Preserve old thread history, but remove its ability to receive global dispatches.
    conn.execute("delete from agent_bindings where role != '编导'")
    conn.execute("""
        update agent_thread_bindings set status='inactive', is_primary=0, updated_at=?
        where role != '编导'
          and thread_id not in (select thread_id from creative_team_bindings)
    """, (now,))
    for role, item in bindings.items():
        old = conn.execute("select thread_id from creative_team_bindings where owner_thread_id=? and role=?",
                           (owner, role)).fetchone()
        if old and old["thread_id"] != item["thread_id"]:
            conn.execute("update agent_thread_bindings set status='inactive', is_primary=0 where role=? and thread_id=?",
                         (role, old["thread_id"]))
        config = FIXED_ROLES[role]
        conn.execute("""
            insert or ignore into agents values (?, ?, 'fixed', null, 'active', ?, ?, ?, ?)
        """, (role, config["agent_id"], json.dumps(config["capabilities"], ensure_ascii=False),
              config["write_scope"], now, now))
        conn.execute("""
            insert into creative_team_bindings values (?, ?, ?, ?, ?)
            on conflict(owner_thread_id, role) do update set
            thread_id=excluded.thread_id, host_id=excluded.host_id, updated_at=excluded.updated_at
        """, (owner, role, item["thread_id"], item["host_id"], now))
        conn.execute("""
            insert into agent_thread_bindings values (?, ?, ?, 'active', 0, ?, ?)
            on conflict(role, thread_id) do update set
            host_id=excluded.host_id, status='active', is_primary=0, updated_at=excluded.updated_at
        """, (role, item["thread_id"], item["host_id"], now, now))

    # Pending messages may carry a previously prepared shared target. Never replay it.
    pending = conn.execute("select * from dispatches where status='pending' and to_role != '编导'").fetchall()
    pending_ids = {d["dispatch_id"] for d in pending}
    for cached in conn.execute("select * from idempotency where operation='dispatch_prepare'").fetchall():
        if json.loads(cached["response_json"]).get("dispatch_id") in pending_ids:
            conn.execute("insert or ignore into retired_dispatch_prepare_cache values (?, ?, ?)",
                         (cached["request_id"], cached["response_json"], now))
            conn.execute("delete from idempotency where request_id=?", (cached["request_id"],))
    for dispatch in pending:
        binding = target(conn, dispatch["project_id"], dispatch["to_role"])
        conn.execute("""
            update dispatches set target_thread_id=?, target_host_id=?,
            prepared_thread_id=null, prepared_host_id=null, prepare_token=null, prepared_at=null,
            updated_at=? where dispatch_id=?
        """, (binding["thread_id"] if binding else None, binding["host_id"] if binding else None,
              now, dispatch["dispatch_id"]))
    service._sync_ledgers(conn)
    for project_id in {d["project_id"] for d in pending}:
        service._sync_project_files(conn, project_id)
    teams = list_teams(conn)
    lines = ["# 编导执行团队", "", "按所属编导隔离派发；旧通用执行任务不再接单。", "",
             "| 团队 | 编导 | 角色 | 执行任务 | 主机 |", "| --- | --- | --- | --- | --- |"]
    for team in teams:
        for role, item in team["bindings"].items():
            lines.append(f"| {team['label']} | {team['owner_thread_id']} | {role} | {item['thread_id']} | {item['host_id']} |")
    service._write_text(service.ledger_root / "编导执行团队.md", "\n".join(lines) + "\n")
    return next(t for t in teams if t["owner_thread_id"] == owner)


def list_teams(conn):
    result = []
    for row in conn.execute("select * from creative_director_teams order by label"):
        team = dict(row)
        team["bindings"] = {
            b["role"]: {"thread_id": b["thread_id"], "host_id": b["host_id"]}
            for b in conn.execute("select * from creative_team_bindings where owner_thread_id=? order by role",
                                  (row["owner_thread_id"],))
        }
        result.append(team)
    return result
