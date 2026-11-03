(function() {
    "use strict";
    var C = {
        furina: null,
        fuDetail: null,
        fuId: "",
        fuTab: "input",
        fuSequence: 0,
        diagnostics: null,
        diagnosticChat: "",
        diagnosticStatus: "",
        organizer: null,
        orgChat: "",
        orgSequence: 0,
        memoryDirty: false,
        orgWriting: false,
        planner: null,
        plannerWriting: false,
        plannerSequence: 0,
        plannerQueued: false,
        plannerSubmitError: "",
        plannerSuccess: "",
        replanId: "",
        models: null,
        busy: {},
        loaded: {},
        errors: {}
    };
    var $ = function(id) {
        return document.getElementById(id);
    };
    var esc = function(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function(c) {
            return {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#39;"
            }[c];
        });
    };
    var num = function(n) {
        return n !== null && n !== undefined && n !== "" && Number.isFinite(Number(n));
    };
    var count = function(n) {
        return num(n) ? Number(n).toLocaleString("zh-CN") : "—";
    };
    var clock = function(t) {
        return num(t) ? new Date(Number(t) * 1e3).toLocaleTimeString("zh-CN", {
            hour12: false
        }) : "—";
    };
    var ms = function(n) {
        return num(n) ? (Number(n) / 1e3).toFixed(1) + " 秒" : "—";
    };
    var txt = function(id, value) {
        var el = $(id), s = String(value == null ? "" : value);
        if (el.textContent !== s) el.textContent = s;
    };
    var empty = function(s) {
        return '<div class="cu-empty">' + esc(s) + "</div>";
    };
    var statuses = {
        received: "已收到",
        queued: "等待处理",
        skipped: "未触发",
        generating: "生成中",
        sending: "发送中",
        sent: "已发送",
        failed: "失败",
        open: "待完成",
        overdue: "已过期",
        completed: "已完成",
        archived: "已归档",
        active: "有效",
        expired: "已到期",
        complete: "已完成",
        running: "进行中",
        error: "失败",
        interrupted: "已中断"
    };
    function tag(status, label) {
        return '<span class="cu-tag" data-status="' + esc(status) + '">' + esc(label || statuses[status] || status || "—") + "</span>";
    }
    function notice(id, message, kind) {
        var el = $(id);
        txt(id, message || "");
        el.hidden = !message;
        el.dataset.kind = kind || "";
    }
    function options(items, selected, first) {
        return (first ? '<option value="">' + esc(first) + "</option>" : "") + (items || []).map(function(x) {
            var id = x.id || x.name;
            return '<option value="' + esc(id) + '"' + (id === selected ? " selected" : "") + ">" + esc(x.name || id) + "</option>";
        }).join("");
    }
    function api(path, body) {
        var controller = new AbortController, timer = setTimeout(function() {
            controller.abort();
        }, 12e3), opts = {
            cache: "no-store",
            signal: controller.signal
        };
        if (body !== undefined) {
            opts.method = "POST";
            opts.headers = {
                "Content-Type": "application/json"
            };
            opts.body = JSON.stringify(body);
        }
        return fetch(path, opts).then(function(r) {
            return r.json().catch(function() {
                throw new Error("服务暂时无法返回可读取的数据，请稍后重试。");
            }).then(function(j) {
                if (!r.ok || j.ok === false) {
                    var e = new Error(j.message || {
                        403: "请从本机看板重试。",
                        409: "内容刚有更新，已保留你的草稿，请重新读取后保存。",
                        429: "当前正在冷却，请稍后再试。"
                    }[r.status] || "暂时无法完成操作");
                    e.status = r.status;
                    e.payload = j;
                    throw e;
                }
                return j;
            });
        }).catch(function(e) {
            if (e.name === "AbortError") throw new Error("连接超时。提交结果尚未确认，请读取最新状态，避免重复提交。");
            if (e.name === "TypeError") throw new Error("无法连接本机服务，请检查机器人是否仍在运行。");
            throw e;
        }).finally(function() {
            clearTimeout(timer);
        });
    }
    function read(key, path, success, errorId, force) {
        if (C.busy[key] || !force && Date.now() - (C.loaded[key] || 0) < 4500) return;
        C.busy[key] = true;
        return api(path).then(function(j) {
            C.loaded[key] = Date.now();
            if (errorId) notice(errorId, "");
            success(j);
        }).catch(function(e) {
            if (errorId) notice(errorId, "暂时无法读取：" + e.message);
        }).finally(function() {
            C.busy[key] = false;
        });
    }
    function go(view) {
        if (typeof switchView === "function") switchView(view); else location.hash = view;
    }
    function renderFu() {
        var f = C.furina;
        if (!f) return;
        var st = f.state || {}, w = f.weather || {}, cur = f.currentSegment, next = f.nextSegment;
        txt("fuDate", (f.date || "") + (f.chat ? " · " + f.chat : ""));
        txt("fuPhase", "角色状态 · 非手环数据");
        txt("fuCurrent", cur && cur.activity ? cur.activity : "此刻没有可用的日程安排");
        txt("fuCurrentMeta", cur ? [ cur.from && cur.to ? cur.from + " — " + cur.to : "", cur.place || "", cur.detail || "" ].filter(Boolean).join(" · ") : "可以在日程页生成或调整今天的安排。");
        txt("fuNext", next ? "接下来 · " + (next.from || "") + " " + (next.activity || "") : "今天剩余安排已结束，或暂未生成");
        var defs = [ [ "心情", "mood", [ "", "低落", "偏低", "平稳", "不错", "很好" ], "", "分值越高，心情越好" ], [ "疲劳", "fatigue", [ "", "轻松", "略有疲劳", "有点累", "比较疲惫", "很疲惫" ], "tired", "分值越高，疲劳越明显" ], [ "嗓子", "voice", [ "", "状态较差", "需要休息", "一般", "不错", "状态很好" ], "voice", "沿用生活状态中的嗓子分值" ] ];
        $("fuStatusCards").innerHTML = defs.map(function(d) {
            var v = num(st[d[1]]) ? Math.max(1, Math.min(5, Number(st[d[1]]))) : null;
            return '<div class="fu-state-card" data-tone="' + d[3] + '"><div class="fu-state-label">' + d[0] + '</div><div class="fu-state-value">' + (v ? d[2][Math.round(v)] + "<small>" + v + " / 5</small>" : "暂无记录") + '</div><div class="fu-meter"><i style="width:' + (v ? v * 20 : 0) + '%"></i></div><div class="fu-state-note">' + d[4] + "</div></div>";
        }).join("") + '<div class="fu-state-card"><div class="fu-state-label">天气与生活节律</div><div class="fu-state-value">' + esc(typeof w === "string" ? w : w.kind || w.weather || "暂无天气") + '</div><div class="fu-state-note">' + esc(f.period || "暂无节律记录") + "</div></div>";
        var ps = (f.promises || []).filter(function(p) {
            return !p.legacy && p.chat === f.chat && (p.status === "open" || p.status === "overdue");
        });
        $("fuPromises").innerHTML = ps.length ? ps.slice(0, 5).map(function(p) {
            return '<div class="cu-record"><div class="cu-record-head">' + tag(p.status) + '<span class="cu-record-meta">' + esc(p.due || "未设到期日") + '</span></div><div class="cu-record-text">' + esc(p.promise || "") + "</div></div>";
        }).join("") : empty("没有待兑现的承诺");
        var p = f.primary || {};
        txt("fuModelName", p.model || "暂无模型配置");
        txt("fuModelEffort", (p.effort || "未返回强度") + " · " + (p.provider || "主模型"));
        $("fuModelStats").innerHTML = "<div><span>上下文消息</span><strong>" + count(p.contextMessages) + "</strong></div><div><span>长期记忆字符</span><strong>" + count(p.memoryChars) + "</strong></div>";
        var calls = f.recentCalls || [], selected = C.fuId;
        if (!calls.some(function(x) {
            return x.id === selected;
        })) selected = calls.length ? calls[0].id : "";
        $("fuCallSelect").innerHTML = calls.length ? calls.map(function(x) {
            return '<option value="' + esc(x.id) + '"' + (x.id === selected ? " selected" : "") + ">" + esc(clock(x.startedAt) + " · " + (x.purpose || "chat") + " · " + (statuses[x.status] || x.status || "") + " · " + (x.model || p.model || "")) + "</option>";
        }).join("") : '<option value="">尚无能够确认属于此会话的调用</option>';
        if (selected !== C.fuId) {
            C.fuId = selected;
            C.fuDetail = null;
            if (selected) loadFuTrace(selected); else renderFuTrace();
        } else if (selected && C.fuDetail && C.fuDetail.status === "running") loadFuTrace(selected);
    }
    function loadFu(force) {
        return read("furina", "/api/companion/furina", function(j) {
            C.furina = j;
            renderFu();
        }, "furinaError", force);
    }
    function loadFuTrace(id) {
        var seq = ++C.fuSequence;
        txt("fuTraceMeta", "正在读取这次调用…");
        return api("/api/dashboard/traces?id=" + encodeURIComponent(id)).then(function(j) {
            if (seq !== C.fuSequence || id !== C.fuId) return;
            C.fuDetail = j.call;
            renderFuTrace();
        }).catch(function(e) {
            if (seq === C.fuSequence) txt("fuTraceMeta", "无法读取调用：" + e.message);
        });
    }
    function renderFuTrace() {
        var d = C.fuDetail, content = "等待可关联的调用记录。", actual = "";
        if (d) {
            actual = C.fuTab === "input" ? d.inputText || JSON.stringify(d.request || {}, null, 2) : C.fuTab === "reasoning" ? d.reasoning || "" : d.output || "";
            content = actual || (C.fuTab === "reasoning" ? "此模型本次没有返回思考文本。" : d.status === "running" ? "等待接口返回…" : "本次没有返回文本。");
            txt("fuTraceMeta", [ d.model, d.purpose, statuses[d.status] || d.status, clock(d.startedAt), ms(d.ms), actual ? String(actual).length + " 字符" : "没有返回该项文本" ].filter(Boolean).join(" · "));
        } else txt("fuTraceMeta", "仅显示能确认属于芙宁娜所在会话的调用；历史未关联记录仍可在模型页查看。");
        txt("fuTraceText", content);
        txt("fuTraceRequest", d ? JSON.stringify(d.request || {}, null, 2) : "—");
        txt("fuTraceResponse", d ? JSON.stringify(d.rawResponse || {}, null, 2) : "—");
        document.querySelectorAll('[data-cu="fu-tab"]').forEach(function(b) {
            var active = b.dataset.tab === C.fuTab;
            b.classList.toggle("selected", active);
            b.setAttribute("aria-selected", String(active));
        });
        document.querySelector('[data-cu="fu-copy"]').disabled = !actual;
    }
    function stat(label, value, tone) {
        return '<div class="cu-stat" data-tone="' + (tone || "") + '"><div class="cu-stat-label">' + esc(label) + '</div><div class="cu-stat-value">' + count(value) + "</div></div>";
    }
    function renderDiagnostics() {
        var d = C.diagnostics;
        if (!d) return;
        var counts = d.counts || {};
        $("diagnosticsCounts").innerHTML = stat("收到消息", counts.received) + stat("实际发送", counts.sent, "ok") + stat("正常未触发", counts.skipped, "warn") + stat("处理失败", counts.failed, "bad");
        var select = $("diagnosticsChat");
        if (document.activeElement !== select) select.innerHTML = options(d.chats, C.diagnosticChat, "全部会话");
        select.value = C.diagnosticChat;
        txt("diagnosticsUpdated", "更新于 " + clock(d.now));
        var list = (d.messages || []).filter(function(m) {
            return !C.diagnosticStatus || (C.diagnosticStatus === "active" ? [ "received", "queued", "generating", "sending" ].indexOf(m.status) >= 0 : m.status === C.diagnosticStatus);
        });
        var opened = {};
        document.querySelectorAll("#diagnosticsList details[open]").forEach(function(x) {
            opened[x.dataset.messageId] = true;
        });
        $("diagnosticsList").innerHTML = list.length ? list.map(function(m) {
            var steps = m.steps || [], stageNames = {
                received: "收到消息",
                trigger: "触发判断",
                model: "模型生成",
                send: "微信发送"
            };
            return '<details class="cu-diagnostic" data-message-id="' + esc(m.id) + '"' + (opened[m.id] ? " open" : "") + '><summary class="cu-message-summary"><div class="cu-message-main"><div class="cu-record-head"><strong>' + esc(m.chat || "未知会话") + '</strong><span class="cu-record-meta">' + esc(m.sender || "") + " · " + clock(m.receivedAt) + '</span></div><div class="cu-record-text">' + esc(m.preview || "未保存消息预览") + '</div><div class="cu-small-note">' + esc(m.reason || m.reasonCode || "等待下一步") + "</div></div>" + tag(m.status) + '</summary><div class="cu-steps">' + [ "received", "trigger", "model", "send" ].map(function(stage) {
                var hits = steps.filter(function(x) {
                    return x.stage === stage;
                }), s = hits.length ? hits[hits.length - 1] : null;
                return '<div class="cu-step" data-state="' + esc(s ? s.status : "waiting") + '"><div class="cu-step-label">' + stageNames[stage] + '</div><div class="cu-step-message">' + esc(s ? s.message || s.reasonCode || "已记录" : "尚未进入") + '</div><div class="cu-step-time">' + (s ? clock(s.at) : "—") + "</div></div>";
            }).join("") + "</div>" + ((m.traceIds || []).length ? '<div class="cu-trace-links">' + m.traceIds.map(function(id) {
                return '<button class="cu-link" data-cu="open-trace" data-id="' + esc(id) + '">查看关联模型原文 ↗</button>';
            }).join("") + "</div>" : "") + "</details>";
        }).join("") : empty("当前条件下没有消息。新收到的消息会在这里出现。");
    }
    function loadDiagnostics(force) {
        return read("diagnostics", "/api/companion/diagnostics?limit=80" + (C.diagnosticChat ? "&chat=" + encodeURIComponent(C.diagnosticChat) : ""), function(j) {
            C.diagnostics = j;
            renderDiagnostics();
        }, "diagnosticsError", force);
    }
    function renderOrganizer() {
        var o = C.organizer;
        if (!o) return;
        var list = o.chats || [];
        $("organizerChat").innerHTML = options(list, C.orgChat, "历史未归属");
        $("organizerChat").value = C.orgChat;
        var ct = o.counts || {};
        $("organizerCounts").innerHTML = stat("待完成", ct.open) + stat("已过期", ct.overdue, "warn") + stat("已完成", ct.completed, "ok") + stat("历史未归属", ct.legacy);
        if (!C.memoryDirty) $("memoryEditor").value = (o.memory || {}).text || "";
        $("memoryEditor").disabled = !C.orgChat || C.orgWriting;
        document.querySelector('[data-cu="memory-save"]').disabled = !C.orgChat || C.orgWriting;
        document.querySelectorAll("#factForm input,#factForm textarea,#factForm button").forEach(function(el) {
            el.disabled = !C.orgChat || C.orgWriting;
        });
        $("memoryDirty").hidden = !C.memoryDirty;
        var facts = o.facts || [];
        $("factList").innerHTML = facts.length ? facts.map(function(f) {
            return '<div class="cu-record"><div class="cu-record-head">' + tag(f.status) + "<span>" + esc(f.personName || f.personId || "整个会话") + '</span></div><div class="cu-record-text">' + esc(f.text) + '</div><div class="cu-record-meta">' + esc(f.personId || "会话内共有事实") + (f.expiresAt ? " · 到期 " + esc(f.expiresAt) : "") + '</div><div class="cu-record-actions"><button class="cu-link" data-cu="fact-edit" data-id="' + esc(f.id) + '">编辑</button><button class="cu-link" data-cu="fact-status" data-id="' + esc(f.id) + '" data-status="' + (f.status === "archived" || f.status === "expired" ? "active" : "archived") + '">' + (f.status === "archived" || f.status === "expired" ? "重新启用" : "归档") + "</button></div></div>";
        }).join("") : empty(C.orgChat ? "还没有单独记录的事实" : "请先选择一个会话，再记录事实");
        var filter = $("promiseFilter").value, ps = (o.promises || []).filter(function(p) {
            return filter === "all" || (filter === "active" ? p.status === "open" || p.status === "overdue" : p.status === filter);
        });
        $("organizerPromises").innerHTML = ps.length ? ps.map(function(p) {
            var scope = p.legacy || !p.chat;
            return '<div class="cu-record"><div class="cu-record-head">' + tag(p.status) + (scope ? tag("archived", "未归属") : "") + '<span class="cu-record-meta">' + esc(p.due || "未设到期日") + (p.personName ? " · " + esc(p.personName) : "") + '</span></div><div class="cu-record-text">' + esc(p.promise || "") + "</div>" + (scope ? '<div class="cu-small-note">历史记录不会自动归到某个人或注入其他会话。</div><div class="cu-record-actions"><select class="cu-promise-assignment" data-promise-assign="' + esc(p.id) + '" aria-label="为承诺选择归属会话">' + options(list, "", "选择归属会话") + '</select><button class="cu-link" data-cu="promise-assign" data-id="' + esc(p.id) + '">设置归属</button></div>' : "") + '<div class="cu-record-actions">' + (p.status !== "completed" ? '<button class="cu-link" data-cu="promise-status" data-id="' + esc(p.id) + '" data-status="completed">标为已完成</button>' : "") + (p.status === "completed" || p.status === "archived" ? '<button class="cu-link" data-cu="promise-status" data-id="' + esc(p.id) + '" data-status="open">重新打开</button>' : "") + (p.status !== "archived" ? '<button class="cu-link" data-cu="promise-status" data-id="' + esc(p.id) + '" data-status="archived">归档</button>' : "") + "</div></div>";
        }).join("") : empty("当前筛选下没有承诺");
    }
    function loadOrganizer(force) {
        if (C.orgWriting) return Promise.resolve();
        var seq = ++C.orgSequence, chat = C.orgChat;
        if (!C.organizer || C.organizer.chat !== chat) {
            $("memoryEditor").value = "";
            $("memoryEditor").disabled = true;
            document.querySelectorAll('#factForm input,#factForm textarea,#factForm button,[data-cu="memory-save"]').forEach(function(el) {
                el.disabled = true;
            });
            $("factList").innerHTML = empty("正在读取此会话…");
            $("organizerPromises").innerHTML = empty("正在读取此会话…");
        }
        return api("/api/companion/organizer?chat=" + encodeURIComponent(chat)).then(function(j) {
            if (seq !== C.orgSequence || chat !== C.orgChat) return;
            C.organizer = j;
            C.loaded.organizer = Date.now();
            renderOrganizer();
        }).catch(function(e) {
            if (seq === C.orgSequence) notice("organizerMessage", "无法读取：" + e.message);
        });
    }
    function writeOrganizer(action, data) {
        if (C.orgWriting || !C.organizer) return Promise.resolve(false);
        C.orgSequence++;
        C.orgWriting = true;
        renderOrganizer();
        notice("organizerMessage", "正在保存…");
        var chat = C.orgChat, body = Object.assign({
            action: action,
            chat: chat,
            revision: C.organizer.revision
        }, data || {});
        return api("/api/companion/organizer", body).then(function(j) {
            if (chat !== C.orgChat) return false;
            if (action === "memory_save") C.memoryDirty = false;
            C.organizer = j.organizer || j;
            renderOrganizer();
            notice("organizerMessage", action === "tidy" ? "整理完成。重复记录归档，到期状态已更新。" : "已保存。", "success");
            C.loaded.furina = 0;
            return true;
        }).catch(function(e) {
            notice("organizerMessage", e.message);
            if (e.status === 409) {
                C.orgWriting = false;
                loadOrganizer(true);
            }
            return false;
        }).finally(function() {
            C.orgWriting = false;
            renderOrganizer();
        });
    }
    function resetFact() {
        [ "factId", "factPersonId", "factPersonName", "factText", "factExpiry" ].forEach(function(id) {
            $(id).value = "";
        });
    }
    function renderPlanner() {
        var p = C.planner;
        if (!p) return;
        var g = p.generation || {}, running = g.state === "running", blocked = C.plannerWriting || running, cooling = g.state === "cooldown" && Number(g.retryAfterSeconds) > 0;
        $("replanRemainingButton").disabled = blocked || cooling || !(p.timeline || []).length;
        var hasFuture = (p.timeline || []).some(function(x) {
            return !x.elapsed && !x.locked;
        });
        if (!hasFuture) $("replanRemainingButton").disabled = true;
        var versions = p.versions || [], selected = $("plannerVersion").value;
        $("plannerVersion").innerHTML = versions.length ? versions.map(function(v) {
            return '<option value="' + esc(v.id) + '"' + (v.id === selected ? " selected" : "") + ">" + esc((v.label || "保存的日程") + " · " + clock(v.createdAt)) + "</option>";
        }).join("") : '<option value="">还没有可恢复的版本</option>';
        $("plannerRestoreButton").disabled = blocked || !versions.length;
        var locked = (p.timeline || []).filter(function(x) {
            return x.locked;
        }).length;
        txt("plannerLockCount", locked + " 段已锁定");
        $("plannerUnlockAll").disabled = blocked || !locked;
        var list = (p.timeline || []).filter(function(x) {
            return $("plannerShowPast").checked || !x.elapsed;
        });
        $("plannerSegments").innerHTML = list.length ? list.map(function(x) {
            return '<div class="cu-segment" data-past="' + String(!!x.elapsed) + '"><span class="cu-segment-time">' + esc(x.from) + " — " + esc(x.to) + '</span><div class="cu-segment-text">' + esc(x.activity) + (x.place ? '<div class="cu-record-meta">' + esc(x.place) + "</div>" : "") + '</div><button class="cu-button cu-segment-lock" data-cu="planner-lock" data-id="' + esc(x.id) + '" data-locked="' + String(!x.locked) + '" aria-pressed="' + String(!!x.locked) + '"' + (blocked || x.elapsed ? " disabled" : "") + ">" + (x.elapsed ? "自动保留" : x.locked ? "已锁定" : "锁定") + "</button></div>";
        }).join("") : empty("没有可调整的未来安排");
        if (C.plannerSubmitError) notice("plannerMessage", C.plannerSubmitError); else if (running) notice("plannerMessage", C.replanId && C.replanId === g.jobId ? "后台正在重排剩余时间，已开始和锁定的安排会保留。" : "后台有日程任务正在运行，完成后可继续调整。"); else if (C.replanId && C.replanId === g.jobId && g.state === "succeeded") notice("plannerMessage", "剩余时间已重排完成，已开始与锁定的安排保留。", "success"); else if (C.replanId && C.replanId === g.jobId && (g.state === "failed" || g.state === "cooldown")) notice("plannerMessage", (g.message || "重排失败") + "，旧日程已保留。"); else if (C.plannerSuccess) notice("plannerMessage", C.plannerSuccess, "success");
    }
    function loadPlanner(force) {
        if (C.busy.planner) {
            if (force) C.plannerQueued = true;
            return Promise.resolve();
        }
        if (!force && Date.now() - (C.loaded.planner || 0) < 4500) return Promise.resolve();
        C.busy.planner = true;
        var seq = C.plannerSequence;
        return api("/api/companion/planner").then(function(j) {
            if (seq !== C.plannerSequence) {
                C.plannerQueued = true;
                return;
            }
            C.planner = j;
            C.loaded.planner = Date.now();
            notice("plannerMessage", "");
            renderPlanner();
        }).catch(function(e) {
            if (seq === C.plannerSequence) notice("plannerMessage", "无法读取日程：" + e.message);
        }).finally(function() {
            C.busy.planner = false;
            if (C.plannerQueued) {
                C.plannerQueued = false;
                loadPlanner(true);
            }
        });
    }
    function writePlanner(action, data) {
        if (C.plannerWriting || !C.planner) return;
        C.plannerSequence++;
        C.plannerWriting = true;
        C.plannerSubmitError = "";
        C.plannerSuccess = "";
        renderPlanner();
        notice("plannerMessage", "正在处理…");
        return api("/api/companion/planner", Object.assign({
            action: action,
            revision: C.planner.revision
        }, data || {})).then(function(j) {
            C.plannerSuccess = action === "replan_remaining" ? "已在后台重排剩余时间，页面会自动更新。" : action === "restore" ? "已恢复所选版本，并保存恢复前的安排。" : "锁定设置已保存。";
            if (action === "replan_remaining" && j.generation) C.replanId = j.generation.jobId || "";
            notice("plannerMessage", C.plannerSuccess, "success");
            if (j.planner) C.planner = j.planner;
            return loadPlanner(true);
        }).catch(function(e) {
            C.plannerSubmitError = e.message;
            notice("plannerMessage", e.message);
            if (e.status === 409) loadPlanner(true);
        }).finally(function() {
            C.plannerWriting = false;
            renderPlanner();
            if (typeof loadLife === "function") loadLife();
            C.loaded.furina = 0;
        });
    }
    function renderModels() {
        var j = C.models;
        if (!j) return;
        txt("modelUsageWindow", j.windowLabel || "最近保留的实际调用");
        $("companionRoutes").innerHTML = (j.routes || []).map(function(r) {
            return '<div class="cu-route"><div class="cu-route-label">' + esc(r.label || r.purpose) + '</div><div class="cu-route-model">' + esc(r.model || "未配置") + '</div><div class="cu-route-meta">' + esc([ r.provider, r.effort ? "推理 " + r.effort : "", r.enabled === false ? "未启用" : "" ].filter(Boolean).join(" · ")) + "</div></div>";
        }).join("");
        var metrics = j.metrics || [];
        $("modelUsageRows").innerHTML = metrics.length ? metrics.map(function(m) {
            var image = m.purpose === "image_generate" || /qwen-image/.test(m.model || ""), usage = image ? num(m.outputImages) ? count(m.inputImages) + " / " + count(m.outputImages) + " 张" : "图片张数见调用详情" : count(m.inputTokens) + " / " + count(m.outputTokens) + " tokens";
            return "<tr><td>" + esc(m.model) + "<small>" + esc(m.purpose || "未知任务") + "</small></td><td>" + esc(m.effort || "—") + "</td><td>" + count(m.success) + " / " + count(m.failed) + " / " + count(m.running) + "</td><td>" + (num(m.successRate) ? (Number(m.successRate) <= 1 ? Number(m.successRate) * 100 : Number(m.successRate)).toFixed(1) + "%" : "—") + "</td><td>" + ms(m.medianMs) + "</td><td>" + ms(m.p95Ms) + "</td><td>" + usage + "</td></tr>";
        }).join("") : '<tr><td colspan="7">还没有可统计的调用</td></tr>';
    }
    function loadModels(force) {
        return read("models", "/api/companion/models", function(j) {
            C.models = j;
            renderModels();
        }, "modelUsageError", force);
    }
    function activeView() {
        return typeof S !== "undefined" ? S.view : String(location.hash).slice(1);
    }
    function onView(v, force) {
        if (v === "furina") loadFu(force); else if (v === "diagnostics") loadDiagnostics(force); else if (v === "memory" && (!C.organizer || force)) loadOrganizer(force); else if (v === "life") loadPlanner(force); else if (v === "model") loadModels(force);
    }
    document.addEventListener("click", function(event) {
        var b = event.target.closest("[data-cu]");
        if (!b || b.disabled) return;
        var a = b.dataset.cu;
        if (a === "goto") go(b.dataset.view); else if (a === "furina-refresh") loadFu(true); else if (a === "fu-organizer") {
            if (C.memoryDirty) {
                go("memory");
                notice("organizerMessage", "请先保存或撤销未保存的记忆，再切换会话。");
                return;
            }
            C.orgChat = C.furina && C.furina.chat || "";
            C.organizer = null;
            go("memory");
        } else if (a === "fu-tab") {
            C.fuTab = b.dataset.tab;
            renderFuTrace();
        } else if (a === "fu-copy") {
            if (navigator.clipboard) navigator.clipboard.writeText($("fuTraceText").textContent).then(function() {
                b.textContent = "已复制";
                setTimeout(function() {
                    b.textContent = "复制原文";
                }, 1500);
            }).catch(function() {
                txt("fuTraceMeta", "复制失败，可以选中文本手动复制。");
            });
        } else if (a === "diagnostics-refresh") loadDiagnostics(true); else if (a === "open-trace") {
            go("model");
            window.dispatchEvent(new CustomEvent("companion:trace", {
                detail: {
                    id: b.dataset.id
                }
            }));
        } else if (a === "organizer-refresh") {
            notice("organizerMessage", C.memoryDirty ? "正在读取最新版本，未保存的草稿会保留。" : "");
            loadOrganizer(true);
        } else if (a === "organizer-tidy") writeOrganizer("tidy"); else if (a === "memory-save") writeOrganizer("memory_save", {
            text: $("memoryEditor").value
        }); else if (a === "memory-discard") {
            C.memoryDirty = false;
            renderOrganizer();
            notice("organizerMessage", "未保存的修改已撤销。");
        } else if (a === "fact-reset") resetFact(); else if (a === "fact-edit") {
            var f = (C.organizer.facts || []).find(function(x) {
                return x.id === b.dataset.id;
            });
            if (f) {
                $("factId").value = f.id;
                $("factPersonId").value = f.personId || "";
                $("factPersonName").value = f.personName || "";
                $("factText").value = f.text || "";
                $("factExpiry").value = f.expiresAt || "";
                $("factText").focus();
            }
        } else if (a === "fact-status") writeOrganizer("fact_status", {
            id: b.dataset.id,
            status: b.dataset.status
        }); else if (a === "promise-status") writeOrganizer("promise_update", {
            id: b.dataset.id,
            status: b.dataset.status
        }); else if (a === "promise-assign") {
            var sel = Array.from(document.querySelectorAll("[data-promise-assign]")).find(function(x) {
                return x.dataset.promiseAssign === b.dataset.id;
            });
            if (!sel || !sel.value) {
                notice("organizerMessage", "请先选择承诺应归属的会话。");
                return;
            }
            writeOrganizer("promise_update", {
                id: b.dataset.id,
                targetChat: sel.value
            });
        } else if (a === "planner-refresh") loadPlanner(true); else if (a === "planner-lock") writePlanner("lock", {
            segmentIds: [ b.dataset.id ],
            locked: b.dataset.locked === "true"
        }); else if (a === "planner-unlock-all") writePlanner("lock", {
            segmentIds: (C.planner.timeline || []).filter(function(x) {
                return x.locked;
            }).map(function(x) {
                return x.id;
            }),
            locked: false
        }); else if (a === "planner-replan") writePlanner("replan_remaining"); else if (a === "planner-restore" && $("plannerVersion").value) writePlanner("restore", {
            versionId: $("plannerVersion").value
        }); else if (a === "models-refresh") loadModels(true);
    });
    $("fuCallSelect").addEventListener("change", function() {
        C.fuId = this.value;
        C.fuDetail = null;
        if (C.fuId) loadFuTrace(C.fuId); else renderFuTrace();
    });
    $("diagnosticsChat").addEventListener("change", function() {
        C.diagnosticChat = this.value;
        loadDiagnostics(true);
    });
    $("diagnosticsStatus").addEventListener("change", function() {
        C.diagnosticStatus = this.value;
        renderDiagnostics();
    });
    $("organizerChat").addEventListener("change", function() {
        if (C.memoryDirty || C.orgWriting) {
            this.value = C.orgChat;
            notice("organizerMessage", "请先保存或撤销未保存的修改，再切换会话。");
            return;
        }
        C.orgChat = this.value;
        C.organizer = null;
        resetFact();
        notice("organizerMessage", "");
        loadOrganizer(true);
    });
    $("memoryEditor").addEventListener("input", function() {
        C.memoryDirty = true;
        $("memoryDirty").hidden = false;
    });
    $("promiseFilter").addEventListener("change", renderOrganizer);
    $("factForm").addEventListener("submit", function(e) {
        e.preventDefault();
        if (!$("factText").value.trim()) return;
        writeOrganizer("fact_upsert", {
            id: $("factId").value || undefined,
            personId: $("factPersonId").value.trim(),
            personName: $("factPersonName").value.trim(),
            text: $("factText").value.trim(),
            expiresAt: $("factExpiry").value
        }).then(function(ok) {
            if (ok) resetFact();
        });
    });
    $("plannerShowPast").addEventListener("change", renderPlanner);
    if (typeof switchView === "function") {
        var baseSwitch = switchView;
        window.switchView = function(v) {
            baseSwitch(v);
            onView(v, false);
        };
    }
    $("refreshActive").addEventListener("click", function() {
        onView(activeView(), true);
    });
    window.addEventListener("hashchange", function() {
        onView(activeView(), false);
    });
    setInterval(function() {
        if (document.hidden) return;
        var v = activeView();
        if (v === "furina") loadFu(false); else if (v === "diagnostics") loadDiagnostics(false); else if (v === "life") loadPlanner(false); else if (v === "model") loadModels(false);
    }, 5e3);
    onView(activeView(), true);
})();