(function() {
    "use strict";
    var V = {
        date: localDate(),
        range: "day",
        chart: "hr",
        health: null,
        healthAt: 0,
        healthBusy: false,
        healthQueued: false,
        traceBusy: false,
        traceQueued: false,
        calls: [],
        models: [],
        model: "",
        selected: "",
        detail: null,
        tab: "input",
        traceAt: 0,
        detailBusy: false,
        detailSequence: 0,
        detailKey: "",
        listKey: ""
    };
    var el = function(id) {
        return document.getElementById(id);
    };
    var e = function(s) {
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
    var number = function(x) {
        return x !== null && x !== undefined && x !== "" && isFinite(Number(x));
    };
    var val = function(x) {
        return number(x) ? Number(x).toLocaleString("zh-CN", {
            maximumFractionDigits: 1
        }) : "—";
    };
    var duration = function(m) {
        return number(m) ? Math.floor(Number(m) / 60) + "小时" + Math.round(Number(m) % 60) + "分" : "—";
    };
    var clock = function(t) {
        return t ? new Date(Number(t) * 1e3).toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false
        }) : "—";
    };
    function age(t) {
        if (!t) return "尚未收到";
        var a = Math.max(0, Date.now() / 1e3 - Number(t));
        return a < 60 ? Math.floor(a) + "秒前" : a < 3600 ? Math.floor(a / 60) + "分钟前" : a < 86400 ? Math.floor(a / 3600) + "小时前" : Math.floor(a / 86400) + "天前";
    }
    function notice(id, s) {
        var n = el(id);
        if (!n) return;
        n.hidden = !s;
        n.textContent = s || "";
    }
    function api(path) {
        var control = new AbortController, timer = setTimeout(function() {
            control.abort();
        }, 1e4);
        return fetch(path, {
            cache: "no-store",
            signal: control.signal
        }).then(function(r) {
            if (!r.ok) throw new Error(r.status === 403 ? "仅支持在本机查看" : r.status === 404 ? "新接口尚未就绪" : "服务暂时不可用（" + r.status + "）");
            return r.json();
        }).then(function(j) {
            if (j.ok === false) throw new Error(j.error || "未能读取数据");
            return j;
        }).finally(function() {
            clearTimeout(timer);
        });
    }
    function empty(text, sub) {
        return '<div class="v2-empty">' + e(text) + (sub ? "<small>" + e(sub) + "</small>" : "") + "</div>";
    }
    function badge(node, text, state) {
        node.textContent = text;
        node.dataset.state = state;
    }
    function chart(points, color, small) {
        points = (points || []).filter(function(p) {
            return number(p.t) && number(p.v);
        }).slice().sort(function(a, b) {
            return a.t - b.t;
        });
        if (!points.length) return small ? "" : empty("这段时间还没有测量记录", "收到数据后，会按测量时间绘制曲线。");
        if (points.length > 350) {
            var stride = Math.ceil(points.length / 350);
            points = points.filter(function(_, i) {
                return i % stride === 0 || i === points.length - 1;
            });
        }
        var w = 800, h = small ? 80 : 220, pad = small ? {
            l: 4,
            r: 4,
            t: 9,
            b: 4
        } : {
            l: 42,
            r: 12,
            t: 15,
            b: 31
        };
        var values = points.map(function(p) {
            return Number(p.v);
        }), lo = Math.min.apply(null, values), hi = Math.max.apply(null, values), span = hi - lo;
        lo = Math.max(0, lo - (span || 10) * .16);
        hi += (span || 10) * .16;
        var a = Number(points[0].t), b = Number(points[points.length - 1].t);
        if (a === b) {
            a -= 1800;
            b += 1800;
        }
        var x = function(t) {
            return pad.l + (Number(t) - a) / (b - a) * (w - pad.l - pad.r);
        }, y = function(v) {
            return h - pad.b - (Number(v) - lo) / (hi - lo) * (h - pad.t - pad.b);
        };
        var coords = points.map(function(p) {
            return x(p.t).toFixed(2) + "," + y(p.v).toFixed(2);
        }), svg = "";
        if (!small) {
            for (var i = 0; i < 4; i++) {
                var n = lo + (hi - lo) * i / 3, yy = y(n);
                svg += '<line x1="' + pad.l + '" x2="' + (w - pad.r) + '" y1="' + yy + '" y2="' + yy + '" stroke="#29384e" stroke-dasharray="3 5"/><text x="' + (pad.l - 9) + '" y="' + (yy + 3) + '" fill="#7f96b5" font-size="10" text-anchor="end">' + Math.round(n) + "</text>";
            }
            for (var k = 0; k < 5; k++) {
                var t = a + (b - a) * k / 4, d = new Date(t * 1e3), label = V.range === "day" ? String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0") : d.getMonth() + 1 + "/" + d.getDate();
                svg += '<text x="' + x(t) + '" y="' + (h - 7) + '" fill="#7f96b5" font-size="10" text-anchor="middle">' + label + "</text>";
            }
        }
        svg += '<polygon points="' + x(points[0].t) + "," + (h - pad.b) + " " + coords.join(" ") + " " + x(points[points.length - 1].t) + "," + (h - pad.b) + '" fill="' + color + '" opacity=".065"/><polyline points="' + coords.join(" ") + '" fill="none" stroke="' + color + '" stroke-width="' + (small ? 3 : 2) + '" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>';
        if (!small) points.forEach(function(p) {
            svg += '<circle cx="' + x(p.t) + '" cy="' + y(p.v) + '" r="4" fill="' + color + '" opacity=".25"><title>' + e(new Date(p.t * 1e3).toLocaleString("zh-CN") + " · " + p.v) + "</title></circle>";
        });
        return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" role="img" aria-label="' + e(small ? "测量趋势" : el("wellChartTitle").textContent + "，" + points.length + "个记录") + '">' + svg + "</svg>";
    }
    function renderActivity(s) {
        var specs = [ {
            label: "步数",
            value: s.steps,
            goal: s.stepsGoal,
            unit: "步",
            color: "#ffb274"
        }, {
            label: "活动消耗",
            value: s.calories,
            goal: s.caloriesGoal,
            unit: "千卡",
            color: "#ff91a4"
        }, {
            label: "活动时长",
            value: s.activeMinutes,
            goal: s.activeGoal,
            unit: "分钟",
            color: "#91dba0"
        } ];
        var svg = '<svg viewBox="0 0 200 200" role="img" aria-label="步数、活动消耗和活动时长三环进度">';
        specs.forEach(function(x, i) {
            var r = 85 - i * 17, len = 2 * Math.PI * r, pct = number(x.value) && Number(x.goal) > 0 ? Math.min(1, Math.max(0, Number(x.value) / Number(x.goal))) : 0;
            svg += '<circle cx="100" cy="100" r="' + r + '" fill="none" stroke="' + x.color + '" stroke-width="10" opacity=".12"/><circle cx="100" cy="100" r="' + r + '" fill="none" stroke="' + x.color + '" stroke-width="10" stroke-dasharray="' + len * pct + " " + len + '" transform="rotate(-90 100 100)" stroke-linecap="round"/>';
        });
        var known = specs.some(function(x) {
            return number(x.value);
        });
        svg += '<text x="100" y="99" text-anchor="middle" fill="#dce9fa" font-size="14">' + (known ? "每一步都算数" : "等待数据") + '</text><text x="100" y="118" text-anchor="middle" fill="#8096b5" font-size="8">ACTIVITY</text></svg>';
        el("activityRings").innerHTML = svg;
        el("activityNumbers").innerHTML = specs.map(function(x) {
            return '<div class="activity-line" style="--line-color:' + x.color + '"><div class="label">' + x.label + '</div><span class="number">' + val(x.value) + '</span><span class="goal">/ ' + val(x.goal) + " " + x.unit + "</span></div>";
        }).join("");
        el("wellDistance").textContent = "距离 " + (number(s.distanceKm) ? val(s.distanceKm) + " 公里" : "—");
        el("wellActivityDate").textContent = V.date.slice(5).replace("-", " / ");
    }
    function renderConnection(c) {
        c = c || {};
        var dev = {
            connected: "已连接",
            disconnected: "已断开",
            unknown: "待上报"
        }, data = {
            live: "数据同步正常",
            stale: "数据延迟",
            offline: "同步已中断",
            never: "等待数据"
        };
        var ds = c.deviceStatus || "unknown", is = c.dataStatus || "never";
        badge(el("deviceState"), dev[ds] || "待上报", ds);
        el("deviceName").textContent = c.deviceName || "小米手环";
        el("deviceModel").textContent = c.deviceModel || "Gadgetbridge 健康推送";
        el("deviceBattery").textContent = number(c.battery) ? "▰ " + val(c.battery) + "%" + (c.charging ? " · 充电中" : "") : "电量未上报";
        el("deviceBluetooth").textContent = ds === "unknown" ? "状态未上报" : dev[ds] || "状态未知";
        el("deviceData").textContent = (data[is] || "等待数据") + " · " + age(c.lastPacketAt);
        el("deviceNote").textContent = ds === "unknown" ? String(c.reason || "").indexOf("expired") >= 0 ? "连接状态已过期，等待手机重新确认。" : "手机尚未上报蓝牙状态；数据同步情况单独显示。" : "手机上报 · 状态更新于 " + clock(c.statusAt);
        el("wellSync").textContent = "最近同步 " + age(c.lastPacketAt) + " · " + (c.bridgeSource || "Gadgetbridge");
        var top = el("wearableTopStatus");
        if (top) badge(top, ds === "connected" ? "手环已连接" : ds === "disconnected" ? "手环已断开" : is === "live" ? "手环数据同步中" : "手环状态待上报", ds === "unknown" ? is : ds);
    }
    function renderHealthV2() {
        var j = V.health || {}, s = j.summary || {}, series = j.series || {};
        renderConnection(j.connection);
        renderActivity(s);
        var specs = [ {
            name: "心率",
            glyph: "♡",
            value: s.heartRate,
            unit: "bpm",
            key: "hr",
            color: "#ff93a4",
            sub: number(s.restingHeartRate) ? "静息心率 " + val(s.restingHeartRate) + " bpm" : "静息心率暂无记录"
        }, {
            name: "睡眠",
            glyph: "☾",
            value: s.sleepMinutes,
            unit: "",
            key: "sleep",
            color: "#b9a1f4",
            sub: number(s.sleepScore) ? "睡眠评分 " + val(s.sleepScore) : "查看下方睡眠分布"
        }, {
            name: "压力",
            glyph: "≈",
            value: s.stress,
            unit: "",
            key: "stress",
            color: "#efa966",
            sub: "按手环记录展示"
        }, {
            name: "血氧",
            glyph: "◇",
            value: s.spo2,
            unit: "%",
            key: "spo2",
            color: "#87c5ed",
            sub: "按测量时刻展示"
        } ];
        el("wellMetrics").innerHTML = specs.map(function(x) {
            var v = x.key === "sleep" && number(x.value) ? Math.floor(Number(x.value) / 60) + "<small>小时</small>" + Math.round(Number(x.value) % 60) + "<small>分</small>" : e(val(x.value)) + "<small>" + x.unit + "</small>";
            var points = series[x.key] || [];
            return '<article class="metric-tile" style="--metric-color:' + x.color + '"><div class="metric-name"><span class="metric-glyph">' + x.glyph + "</span>" + x.name + '</div><div class="metric-value">' + v + '</div><div class="metric-spark">' + chart(points, x.color, true) + '</div><div class="metric-sub">' + (number(x.value) ? e(x.sub) : "暂无测量记录") + "</div></article>";
        }).join("");
        var meta = {
            hr: {
                name: "心率",
                color: "#ff93a4",
                unit: "bpm"
            },
            stress: {
                name: "压力",
                color: "#efa966",
                unit: ""
            },
            spo2: {
                name: "血氧",
                color: "#87c5ed",
                unit: "%"
            },
            steps: {
                name: "步数",
                color: "#ffb274",
                unit: "步"
            }
        }[V.chart];
        el("wellChartTitle").textContent = meta.name + "趋势";
        var pts = series[V.chart] || [];
        el("wellMainChart").innerHTML = chart(pts, meta.color, false);
        var values = pts.filter(function(p) {
            return number(p.v);
        }).map(function(p) {
            return Number(p.v);
        });
        el("wellChartStats").innerHTML = values.length ? [ [ "最低", Math.min.apply(null, values) ], [ "平均", values.reduce(function(a, b) {
            return a + b;
        }, 0) / values.length ], [ "最高", Math.max.apply(null, values) ] ].map(function(a) {
            return "<span>" + a[0] + "<b>" + val(a[1]) + " <small>" + meta.unit + "</small></b></span>";
        }).join("") : "<span>等待这段时间的测量数据</span>";
        el("wellChartSubtitle").textContent = V.date + " · " + {
            day: "当日",
            week: "最近一周",
            month: "最近一月"
        }[V.range] + " · " + values.length + " 个记录";
        var sleep = j.sleep || {}, colors = {
            deep: "#7867cf",
            light: "#a89ae4",
            rem: "#d2b5ed",
            awake: "#e0bf9e"
        }, stageNames = {
            deep: "深睡",
            light: "浅睡",
            rem: "REM",
            awake: "清醒"
        };
        el("wellSleepTotal").textContent = number(sleep.minutes) ? duration(sleep.minutes) : "暂无记录";
        var stages = (sleep.stages || []).filter(function(x) {
            return colors[x.stage] && number(x.from) && number(x.to) && Number(x.to) > Number(x.from);
        }).sort(function(a, b) {
            return a.from - b.from;
        }), stageSums = {
            deep: 0,
            light: 0,
            rem: 0,
            awake: 0
        };
        stages.forEach(function(x) {
            stageSums[x.stage] += (Number(x.to) - Number(x.from)) / 60;
        });
        var sums = Object.keys(colors).map(function(k) {
            return {
                key: k,
                value: number(sleep[k]) ? sleep[k] : stages.length ? Math.round(stageSums[k]) : null
            };
        }), total = sums.reduce(function(a, x) {
            return a + (number(x.value) ? Number(x.value) : 0);
        }, 0);
        el("wellSleepBar").innerHTML = total ? '<div class="sleep-bar" role="img" aria-label="睡眠阶段分布">' + sums.map(function(x) {
            return number(x.value) && Number(x.value) > 0 ? '<span style="width:' + Number(x.value) / total * 100 + "%;background:" + colors[x.key] + '" title="' + stageNames[x.key] + " " + duration(x.value) + '"></span>' : "";
        }).join("") + "</div>" : empty("暂无睡眠阶段记录");
        if (stages.length) {
            var start = Number(stages[0].from), end = Math.max.apply(null, stages.map(function(x) {
                return Number(x.to);
            })), cursor = start, parts = [];
            stages.forEach(function(x) {
                if (x.from > cursor) parts.push('<span style="width:' + (x.from - cursor) / (end - start) * 100 + '%;background:#273147" title="未记录"></span>');
                parts.push('<span style="width:' + (x.to - x.from) / (end - start) * 100 + "%;background:" + colors[x.stage] + '" title="' + stageNames[x.stage] + " " + clock(x.from).slice(0, 5) + "–" + clock(x.to).slice(0, 5) + '"></span>');
                cursor = Math.max(cursor, x.to);
            });
            el("wellSleepBar").innerHTML = '<div class="sleep-bar" role="img" aria-label="按时间顺序的睡眠阶段">' + parts.join("") + '</div><div class="sleep-range-labels"><span>' + clock(start).slice(0, 5) + "</span><span>" + clock(end).slice(0, 5) + "</span></div>";
        }
        el("wellSleepLegend").innerHTML = sums.map(function(x) {
            return '<div style="--stage-color:' + colors[x.key] + '"><i></i>' + stageNames[x.key] + "<b>" + e(number(x.value) ? duration(x.value) : "—") + "</b></div>";
        }).join("");
        var daily = (j.daily || []).slice(-31), max = Math.max.apply(null, [ 1 ].concat(daily.map(function(d) {
            return Number(d.steps) || 0;
        })));
        el("wellDailyBars").innerHTML = daily.length ? daily.map(function(d, i) {
            var known = number(d.steps);
            return '<div class="daily-bar-cell" title="' + e(d.date + " · " + (known ? val(d.steps) + " 步" : "无数据")) + '"><span class="bar ' + (known ? "" : "null-bar") + '" style="height:' + (known ? Math.max(3, Number(d.steps) / max * 88) : 3) + 'px"></span><span>' + (daily.length < 12 || i % 5 === 0 || i === daily.length - 1 ? e(d.date.slice(8)) : "·") + "</span></div>";
        }).join("") : empty("还没有每日活动记录");
        document.querySelectorAll('[data-v2="chart"]').forEach(function(n) {
            n.classList.toggle("selected", n.dataset.value === V.chart);
        });
        document.querySelectorAll('[data-v2="range"]').forEach(function(n) {
            var on = n.dataset.value === V.range;
            n.classList.toggle("selected", on);
            n.setAttribute("aria-pressed", String(on));
        });
        el("wellDate").value = V.date;
        el("wellDate").max = localDate();
        document.querySelector('[data-v2="date-next"]').disabled = V.date >= localDate();
    }
    function loadHealthV2(force) {
        if (V.healthBusy) {
            if (force) V.healthQueued = true;
            return;
        }
        if (!force && Date.now() - V.healthAt < 1e4) return;
        var requestedDate = V.date, requestedRange = V.range;
        V.healthBusy = true;
        return api("/api/dashboard/health?date=" + encodeURIComponent(V.date) + "&range=" + V.range).then(function(j) {
            if (requestedDate !== V.date || requestedRange !== V.range) return;
            V.health = j;
            V.healthAt = Date.now();
            notice("wellError", "");
            renderHealthV2();
        }).catch(function(err) {
            notice("wellError", "健康数据暂时无法更新：" + err.message + "。已有记录会保留。");
            if (!V.health) {
                renderHealthV2();
                el("wellSync").textContent = "等待健康接口连接";
            }
        }).finally(function() {
            V.healthBusy = false;
            if (V.healthQueued || requestedDate !== V.date || requestedRange !== V.range) {
                V.healthQueued = false;
                loadHealthV2(true);
            }
        });
    }
    function modelChoices() {
        var map = {};
        V.models.forEach(function(m) {
            if (m.model) map[m.model] = m.count || 0;
        });
        var c = typeof S !== "undefined" && S.snap && S.snap.model || {};
        [ c.model, c.auxModel, c.visionModel, c.asrModel, c.imageModel ].forEach(function(m) {
            if (m && !Object.prototype.hasOwnProperty.call(map, m)) map[m] = 0;
        });
        return map;
    }
    function renderFilters() {
        var map = modelChoices();
        el("traceModelFilters").innerHTML = '<button class="trace-model-filter ' + (!V.model ? "selected" : "") + '" data-v2="trace-model" data-model="">全部模型</button>' + Object.keys(map).map(function(m) {
            return '<button class="trace-model-filter ' + (V.model === m ? "selected" : "") + '" data-v2="trace-model" data-model="' + e(m) + '">' + e(m) + "<span>" + val(map[m]) + "</span></button>";
        }).join("");
        decorateRoutes();
    }
    function decorateRoutes() {
        var counts = modelChoices();
        document.querySelectorAll("#modelRoutes .route-card").forEach(function(card) {
            var model = card.querySelector(".route-model");
            if (!model) return;
            var name = model.textContent;
            card.dataset.traceModel = name;
            card.setAttribute("role", "button");
            card.setAttribute("tabindex", "0");
            card.setAttribute("aria-label", "查看 " + name + " 的原始调用记录");
            card.classList.toggle("trace-route-selected", V.model === name);
            var count = card.querySelector(".route-call-count");
            if (!count) {
                count = document.createElement("div");
                count.className = "route-call-count";
                card.appendChild(count);
            }
            count.textContent = String(counts[name] || 0) + " 次已记录 · 点击查看原文";
        });
    }
    function renderList() {
        var list = V.calls.filter(function(c) {
            return !V.model || c.model === V.model;
        });
        el("traceCount").textContent = "最近 " + list.length + " 次";
        var key = JSON.stringify([ V.model, V.selected, list.map(function(c) {
            return [ c.id, c.status, c.ms, c.outTokens, c.hasReasoning ];
        }) ]);
        if (key === V.listKey) return;
        V.listKey = key;
        var scroll = el("traceCallList").scrollTop;
        el("traceCallList").innerHTML = list.length ? list.map(function(c) {
            return '<button class="trace-call ' + (V.selected === c.id ? "selected" : "") + '" data-v2="trace-select" data-id="' + e(c.id) + '"><span class="trace-call-top"><span>' + clock(c.startedAt) + " · " + e(c.purpose || "chat") + '</span><i class="trace-call-status" data-state="' + e(c.status) + '" aria-label="' + e(c.status) + '"></i></span><span class="trace-call-model">' + e(c.model) + '</span><div class="trace-call-preview">' + e(c.preview || {
                running: "正在生成…",
                complete: "点击查看完整记录",
                error: "调用失败",
                interrupted: "调用中断"
            }[c.status] || "点击查看") + "</div></button>";
        }).join("") : empty("这个模型还没有调用记录", "发生新调用时会自动出现。");
        el("traceCallList").scrollTop = scroll;
    }
    function traceFailureText(d) {
        var err = d.error || {}, code = err.reasonCode || err.reason_code || err.code || "", kind = err.type || "", http = err.httpStatus;
        var messages = {
            content_rejected: "服务商拒绝了这次请求；相同内容进入 5 分钟冷却，避免连续重试。",
            content_refused: "服务商拒绝了这次请求；相同内容进入 5 分钟冷却，避免连续重试。",
            refusal_cooldown: "相同内容正在拒绝冷却期内，本次没有再向服务商请求。",
            content_rejected_cooldown: "相同内容正在拒绝冷却期内，本次没有再向服务商请求。",
            output_incomplete: "模型输出未完成，没有把部分内容当成有效结果。",
            incomplete_output: "模型输出未完成，没有把部分内容当成有效结果。",
            empty_output: "服务商没有返回可用的输出文本。",
            invalid_response: "服务商返回的响应结构异常。",
            rate_limited: "服务商限流，本次已达到重试上限。",
            timeout: "本次调用超时。",
            network_error: "本次调用遇到网络错误。"
        };
        code = {
            vendor_risk_refusal: "content_rejected",
            refusal: "content_rejected",
            http_429: "rate_limited",
            invalid_json: "invalid_response"
        }[code] || code;
        if (messages[code]) return messages[code];
        if (kind === "AuxRefusalError") return messages.content_rejected;
        if (kind === "AuxIncompleteError") return messages.output_incomplete;
        if (http === 401) return "服务商认证失败，请检查该模型的凭据。";
        if (http === 403) return "服务商拒绝访问，请检查模型权限。";
        if (http === 429) return messages.rate_limited;
        if (http >= 500) return "服务商暂时不可用，本次调用失败。";
        return d.status === "error" ? "本次调用失败，具体返回保留在原文和响应 JSON 中。" : "";
    }
    function displayTrace() {
        var d = V.detail;
        if (!d) {
            notice("traceCallError", "");
            el("traceSelectionHead").textContent = "选择一条调用";
            el("traceOriginalText").textContent = "在左侧选择记录，查看完整内容。";
            return;
        }
        notice("traceCallError", traceFailureText(d));
        var status = {
            running: "生成中",
            complete: "已完成",
            error: "失败",
            interrupted: "已中断"
        }[d.status] || d.status;
        var usage = d.rawResponse && d.rawResponse.usage, usageText = usage && number(usage.output_image_count) ? "输入参考 " + val(usage.input_image_count) + " 张 / 输出 " + val(usage.output_image_count) + " 张" : "输入 " + val(d.inTokens) + " / 输出 " + val(d.outTokens) + " tokens";
        el("traceSelectionHead").innerHTML = "<strong>" + e(d.model) + "</strong>" + e((d.provider || "") + " / " + (d.purpose || "chat") + " · " + clock(d.startedAt) + " · " + status + " · " + (number(d.ms) ? (Number(d.ms) / 1e3).toFixed(1) + " 秒" : "") + " · " + usageText);
        var content = V.tab === "input" ? d.inputText || JSON.stringify(d.request || {}, null, 2) : V.tab === "reasoning" ? d.reasoning || (d.status === "running" ? "正在等待模型返回思考内容…" : "此模型本次没有返回思考过程。") : d.output || (d.error ? "调用失败：" + (typeof d.error === "string" ? d.error : JSON.stringify(d.error)) : d.status === "running" ? "正在等待输出…" : "本次没有文本输出。");
        if (typeof content !== "string") content = JSON.stringify(content, null, 2);
        var pre = el("traceOriginalText"), selected = window.getSelection();
        if (pre.textContent !== content && !(selected && String(selected) && pre.contains(selected.anchorNode))) {
            var scroll = pre.scrollTop;
            pre.textContent = content;
            pre.scrollTop = scroll;
        }
        var returned = V.tab === "input" ? content : V.tab === "reasoning" ? d.reasoning : d.output;
        el("traceTextMeta").textContent = returned ? {
            input: "完整输入文本",
            reasoning: "模型实际返回的思考",
            output: "模型实际返回的输出"
        }[V.tab] + " · " + String(returned).length.toLocaleString("zh-CN") + " 字符" + ((d.media || []).length ? " · 含 " + d.media.length + " 个媒体信息占位" : "") : d.status === "running" ? "等待模型返回内容" : V.tab === "reasoning" ? "本次未返回思考文本" : "本次未返回文本输出";
        document.querySelector('[data-v2="trace-copy"]').disabled = !returned;
        el("traceRequestJson").textContent = JSON.stringify(d.request || {}, null, 2);
        el("traceResponseJson").textContent = JSON.stringify(d.rawResponse || {}, null, 2);
        document.querySelectorAll('[data-v2="trace-tab"]').forEach(function(n) {
            var on = n.dataset.value === V.tab;
            n.classList.toggle("selected", on);
            n.setAttribute("aria-selected", String(on));
        });
    }
    function selectTrace(id, force) {
        if (V.detailBusy && V.selected === id) return;
        if (!force && V.selected === id && V.detail) return;
        V.selected = id;
        V.detailBusy = true;
        var detailSequence = ++V.detailSequence;
        renderList();
        return api("/api/dashboard/traces?id=" + encodeURIComponent(id)).then(function(j) {
            if (V.selected !== id) return;
            V.detail = j.call;
            displayTrace();
            notice("traceError", "");
        }).catch(function(err) {
            notice("traceError", "无法读取调用详情：" + err.message);
        }).finally(function() {
            if (detailSequence === V.detailSequence) V.detailBusy = false;
        });
    }
    function loadTraces(force) {
        if (V.traceBusy) {
            if (force) V.traceQueued = true;
            return;
        }
        if (!force && Date.now() - V.traceAt < 5e3) return;
        var requestedModel = V.model;
        V.traceBusy = true;
        return api("/api/dashboard/traces?limit=100" + (requestedModel ? "&model=" + encodeURIComponent(requestedModel) : "")).then(function(j) {
            if (requestedModel !== V.model) return;
            V.calls = j.calls || [];
            if (!requestedModel) V.models = j.models || []; else (j.models || []).forEach(function(m) {
                var existing = V.models.find(function(x) {
                    return x.model === m.model;
                });
                if (existing) existing.count = m.count; else V.models.push(m);
            });
            V.traceAt = Date.now();
            renderFilters();
            renderList();
            notice("traceError", "");
            var filtered = V.calls.filter(function(c) {
                return !V.model || c.model === V.model;
            });
            if (!V.selected && filtered.length) selectTrace(filtered[0].id); else if (V.selected && V.detail && V.detail.status === "running") selectTrace(V.selected, true);
        }).catch(function(err) {
            notice("traceError", "调用记录暂时无法更新：" + err.message);
        }).finally(function() {
            V.traceBusy = false;
            if (V.traceQueued || requestedModel !== V.model) {
                V.traceQueued = false;
                loadTraces(true);
            }
        });
    }
    function pickModel(model) {
        V.model = model;
        V.selected = "";
        V.detail = null;
        V.listKey = "";
        renderFilters();
        renderList();
        displayTrace();
        var first = V.calls.find(function(c) {
            return !model || c.model === model;
        });
        if (first) selectTrace(first.id);
        loadTraces(true);
    }
    window.addEventListener("companion:trace", function(event) {
        var id = event.detail && event.detail.id;
        if (!id) return;
        V.model = "";
        V.selected = id;
        V.detail = null;
        V.listKey = "";
        renderFilters();
        renderList();
        displayTrace();
        selectTrace(id, true);
        loadTraces(true);
        el("traceExplorer").scrollIntoView({
            block: "start"
        });
    });
    document.addEventListener("click", function(event) {
        var n = event.target.closest("[data-v2]");
        if (n) {
            var action = n.dataset.v2;
            if (action === "range") {
                V.range = n.dataset.value;
                loadHealthV2(true);
            } else if (action === "chart") {
                V.chart = n.dataset.value;
                renderHealthV2();
            } else if (action === "health-refresh") loadHealthV2(true); else if (action === "date-prev" || action === "date-next") {
                var d = new Date(V.date + "T12:00:00");
                d.setDate(d.getDate() + (action === "date-prev" ? -1 : 1));
                V.date = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
                loadHealthV2(true);
            } else if (action === "traces-refresh") loadTraces(true); else if (action === "trace-model") pickModel(n.dataset.model || ""); else if (action === "trace-select") selectTrace(n.dataset.id); else if (action === "trace-tab") {
                V.tab = n.dataset.value;
                displayTrace();
            } else if (action === "trace-copy") {
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(el("traceOriginalText").textContent).then(function() {
                        n.textContent = "已复制";
                        setTimeout(function() {
                            n.textContent = "复制原文";
                        }, 1500);
                    }).catch(function() {
                        n.textContent = "请选择原文复制";
                    });
                } else n.textContent = "请选择原文复制";
            }
            return;
        }
        var card = event.target.closest("#modelRoutes .route-card[data-trace-model]");
        if (card) {
            pickModel(card.dataset.traceModel);
            el("traceExplorer").scrollIntoView({
                behavior: "smooth",
                block: "start"
            });
        }
    });
    el("wellDate").addEventListener("change", function() {
        if (/^\d{4}-\d{2}-\d{2}$/.test(this.value) && this.value <= localDate()) {
            V.date = this.value;
            loadHealthV2(true);
        }
    });
    document.addEventListener("keydown", function(ev) {
        var card = ev.target.closest && ev.target.closest("#modelRoutes [data-trace-model]");
        if (card && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            card.click();
        }
    });
    if (typeof renderModelRoutes === "function") {
        var originalRoutes = renderModelRoutes;
        renderModelRoutes = function(cfg) {
            originalRoutes(cfg);
            decorateRoutes();
        };
    }
    var top = document.createElement("span");
    top.id = "wearableTopStatus";
    top.className = "connection-pill well-topbar-pill";
    top.textContent = "手环状态待上报";
    var refresh = el("refreshActive");
    refresh.parentNode.insertBefore(top, refresh);
    el("wellDate").value = V.date;
    el("wellDate").max = localDate();
    renderHealthV2();
    loadHealthV2(true);
    loadTraces(true);
    setInterval(function() {
        if (document.hidden) return;
        var view = typeof S !== "undefined" ? S.view : "";
        if (view === "health") loadHealthV2(false); else if (Date.now() - V.healthAt > 3e4) loadHealthV2(false);
        if (view === "model") loadTraces(false);
    }, 3e3);
    window.addEventListener("hashchange", function() {
        if (location.hash === "#health") loadHealthV2(false);
        if (location.hash === "#model") loadTraces(false);
    });
})();