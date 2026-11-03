(function() {
    "use strict";
    var C = {
        job: null,
        date: "",
        receivedAt: 0,
        sending: false,
        loading: false,
        queued: false,
        version: 0,
        lastPoll: 0,
        online: false,
        error: "",
        submitError: ""
    };
    var byId = function(id) {
        return document.getElementById(id);
    };
    function seconds() {
        return Date.now() / 1e3;
    }
    function remaining() {
        var n = C.job ? Number(C.job.retryAfterSeconds) || 0 : 0;
        return Math.max(0, Math.ceil(n - (seconds() - C.receivedAt)));
    }
    function duration(n) {
        n = Math.max(0, Math.floor(n));
        return n >= 60 ? Math.floor(n / 60) + "分" + String(n % 60).padStart(2, "0") + "秒" : n + "秒";
    }
    function clock(t) {
        return t ? new Date(Number(t) * 1e3).toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false
        }) : "";
    }
    function onLifePage() {
        return location.hash === "#life";
    }
    function render() {
        var g = C.job, panel = byId("lifeGenerationPanel"), button = byId("lifeRetryButton"), label = byId("lifeRetryLabel"), status = byId("lifeGenerationStatus");
        var running = !!g && g.state === "running", cooling = !!g && g.state === "cooldown" && (!g.date || !C.date || g.date === C.date) && remaining() > 0;
        panel.setAttribute("aria-busy", String(running || C.sending));
        button.disabled = C.sending || running || cooling || !C.online || !g;
        label.textContent = C.sending ? "正在提交…" : running ? "生成中…" : cooling ? "稍后重试 · " + duration(remaining()) : !C.online ? "等待连接…" : "重新生成今日日程";
        var state = g ? g.state : "idle";
        if (state === "cooldown" && !cooling) state = "failed";
        status.dataset.state = state;
        byId("lifeGenerationState").textContent = !C.online ? "状态暂不可用" : !g ? "正在读取" : {
            idle: "可以开始生成",
            running: g.source === "automatic" ? "正在自动生成" : "正在生成",
            succeeded: "生成完成",
            failed: "生成失败",
            cooldown: "等待重试"
        }[state] || "等待更新";
        var timing = "";
        if (running && g.startedAt) timing = "已用 " + duration(seconds() - Number(g.startedAt)); else if (cooling) timing = "还有 " + duration(remaining()); else if (g && g.endedAt) timing = clock(g.endedAt) + " 更新";
        byId("lifeGenerationTime").textContent = timing;
        var message = g && g.message ? String(g.message) : "点击按钮，重新生成今天的日程。";
        if (g && g.state === "cooldown" && !cooling && (!g.date || g.date === C.date)) message = "冷却已结束，可以再次尝试生成。";
        var reasons = {
            network_error: "网络连接失败",
            timeout: "本次请求超时",
            http_401: "模型凭据验证失败",
            http_403: "模型访问受限",
            http_429: "模型服务限流",
            http_5xx: "模型服务暂时不可用",
            invalid_json: "模型返回格式异常",
            invalid_response: "模型返回格式异常",
            empty_output: "模型没有返回可用文本",
            incomplete_output: "模型输出不完整",
            bad_timeline: "生成的时间线不完整"
        };
        if (g && state === "failed" && reasons[g.reasonCode]) message += " · 原因：" + reasons[g.reasonCode];
        if (g && g.date && C.date && g.date !== C.date) message = "当前任务日期：" + g.date + "。" + message;
        if (byId("lifeGenerationMessage").textContent !== message) byId("lifeGenerationMessage").textContent = message;
        byId("lifeGenerationError").hidden = !C.error;
        if (byId("lifeGenerationError").textContent !== C.error) byId("lifeGenerationError").textContent = C.error;
    }
    function accept(g) {
        if (!g || typeof g.state !== "string") return false;
        C.job = g;
        C.receivedAt = seconds();
        return true;
    }
    function readLife() {
        if (C.loading) {
            C.queued = true;
            return;
        }
        C.loading = true;
        C.lastPoll = Date.now();
        var version = C.version, control = new AbortController, timeout = setTimeout(function() {
            control.abort();
        }, 1e4);
        fetch("/api/life", {
            cache: "no-store",
            signal: control.signal
        }).then(function(r) {
            if (!r.ok) throw new Error("read_failed");
            return r.json();
        }).then(function(j) {
            if (version !== C.version) return;
            if (j.ok === false || !accept(j.generation)) throw new Error("status_missing");
            C.date = j.date || C.date;
            C.online = true;
            C.error = C.submitError;
            if (typeof S !== "undefined") {
                S.life = j;
                S.lifeAt = typeof nowSec === "function" ? nowSec() : seconds();
                if (typeof renderLife === "function") renderLife();
            }
            render();
        }).catch(function() {
            if (version !== C.version) return;
            C.online = false;
            C.error = "暂时无法读取生成状态，稍后会自动重连。";
            render();
        }).finally(function() {
            clearTimeout(timeout);
            C.loading = false;
            if (C.queued) {
                C.queued = false;
                readLife();
            }
        });
    }
    function requestGeneration() {
        if (byId("lifeRetryButton").disabled) return;
        C.sending = true;
        C.error = "";
        C.submitError = "";
        C.version++;
        render();
        var control = new AbortController, timeout = setTimeout(function() {
            control.abort();
        }, 1e4);
        fetch("/api/life/regenerate", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: "{}",
            cache: "no-store",
            signal: control.signal
        }).then(function(r) {
            return r.json().then(function(j) {
                return {
                    http: r.status,
                    payload: j
                };
            });
        }).then(function(result) {
            var j = result.payload || {};
            if (j.generation) accept(j.generation);
            if (result.http === 429 && j.generation) {
                C.online = true;
                return;
            }
            if (result.http >= 400 || j.ok === false) {
                C.submitError = result.http === 403 ? "请刷新本机看板后重试。" : typeof j.message === "string" ? j.message : typeof j.error === "string" ? j.error : "暂时无法开始生成，请稍后重试。";
                C.error = C.submitError;
                return;
            }
            C.online = true;
        }).catch(function() {
            C.error = "尚未确认任务是否已开始，正在读取最新状态。";
        }).finally(function() {
            clearTimeout(timeout);
            C.sending = false;
            render();
            readLife();
        });
    }
    byId("lifeRetryButton").addEventListener("click", requestGeneration);
    window.addEventListener("hashchange", function() {
        if (onLifePage()) readLife();
    });
    setInterval(function() {
        render();
        if (document.hidden) return;
        var active = C.job && C.job.state === "running", interval = active ? 2e3 : C.online ? 1e4 : 5e3;
        if ((onLifePage() || active) && Date.now() - C.lastPoll >= interval) readLife();
    }, 1e3);
    render();
    readLife();
})();