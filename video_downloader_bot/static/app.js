/* ============================================================
   Bot Dashboard — fronted mantiqi
   API: /api/me, /api/summary, /api/timeseries, /api/hourly,
        /api/actions, /api/events, /api/users, /api/login
   ============================================================ */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };

  var state = {
    token: localStorage.getItem("dash_token") || "",
    days: 7,
    page: 1,
    perPage: 12,
    search: "",
    sort: "last_seen",
    charts: { activity: null, actions: null, hourly: null },
    authRequired: false,
    timer: null
  };

  var TYPE_LABELS = {
    start: "/start",
    message: "Xabar",
    callback: "Tugma",
    video: "Video",
    music: "Musiqa",
    desc: "Tavsif"
  };

  var ACTION_ORDER = ["video", "music", "desc", "start"];
  var ACTION_COLORS = {
    video: "#4CC9F0",
    music: "#8B7CF6",
    desc: "#F5A524",
    start: "#34D399"
  };

  var AVATAR_COLORS = [
    "#4CC9F0", "#8B7CF6", "#F5A524", "#34D399",
    "#FB7185", "#60A5FA", "#F472B6", "#A3E635"
  ];

  /* ---------------- Yordamchilar ---------------- */
  function num(n) {
    if (n === null || n === undefined || isNaN(n)) return "0";
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function pad(n) { return n < 10 ? "0" + n : String(n); }

  function fmtDate(ts) {
    var d = new Date(ts * 1000);
    return pad(d.getDate()) + "." + pad(d.getMonth() + 1) + "." + d.getFullYear() +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function fmtClock(ts) {
    var d = new Date(ts * 1000);
    return pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function relTime(ts) {
    var diff = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    if (diff < 60) return "hozirgina";
    if (diff < 3600) return Math.floor(diff / 60) + " daq oldin";
    if (diff < 86400) return Math.floor(diff / 3600) + " soat oldin";
    if (diff < 7 * 86400) return Math.floor(diff / 86400) + " kun oldin";
    return fmtDate(ts);
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function avatarColor(id) {
    return AVATAR_COLORS[Math.abs(id) % AVATAR_COLORS.length];
  }

  function initials(u) {
    var name = (u.first_name || u.username || "").trim();
    if (!name) return "?";
    var parts = name.split(/\s+/);
    var ini = parts[0].charAt(0);
    if (parts[1]) ini += parts[1].charAt(0);
    return ini.toUpperCase();
  }

  function displayName(u) {
    var n = [u.first_name, u.last_name].filter(Boolean).join(" ");
    return n || u.username || "Nomsiz";
  }

  var toastTimer = null;
  function toast(msg) {
    var el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 3500);
  }

  /* ---------------- API ---------------- */
  function api(path) {
    return fetch(path, {
      headers: { "X-Auth-Token": state.token }
    }).then(function (res) {
      if (res.status === 401) {
        var err = new Error("unauthorized");
        err.status = 401;
        throw err;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  /* ---------------- Auth ---------------- */
  function showLogin() {
    $("#loginView").hidden = false;
    $("#appView").hidden = true;
    setTimeout(function () { $("#passwordInput").focus(); }, 60);
  }

  function showApp() {
    $("#loginView").hidden = true;
    $("#appView").hidden = false;
  }

  $("#loginForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var btn = $("#loginBtn");
    var pass = $("#passwordInput").value;
    btn.disabled = true;
    btn.textContent = "Tekshirilmoqda…";
    fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pass })
    })
      .then(function (r) { return r.json().then(function (d) { return { s: r.status, d: d }; }); })
      .then(function (x) {
        if (x.s === 200 && x.d.token) {
          state.token = x.d.token;
          localStorage.setItem("dash_token", state.token);
          $("#loginError").hidden = true;
          $("#passwordInput").value = "";
          boot();
        } else {
          $("#loginError").hidden = false;
        }
      })
      .catch(function () {
        $("#loginError").hidden = false;
        $("#loginError").textContent = "Serverga ulanib bo'lmadi.";
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "Kirish";
      });
  });

  /* ---------------- KPI ---------------- */
  function deltaChip(now, before, unitWord) {
    if (before === null || before === undefined) return "";
    var diff = now - before;
    if (diff > 0) return '<span class="delta up">▲ +' + num(diff) + " " + (unitWord || "") + "</span>";
    if (diff < 0) return '<span class="delta down">▼ ' + num(diff) + " " + (unitWord || "") + "</span>";
    return '<span class="delta flat">— o\'zgarmadi</span>';
  }

  function renderKpis(s) {
    var success = s.success_rate === null ? "—" : num(s.success_rate) + "%";
    var cards = [
      {
        cls: "kpi kpi--hero",
        ico: "👥",
        label: "Jami foydalanuvchi",
        value: num(s.total_users),
        sub: '<span class="muted">bugun</span> ' + deltaChip(s.new_today, s.new_yesterday, "ta")
      },
      {
        cls: "kpi",
        ico: "✨",
        label: "Yangi (bugun)",
        value: num(s.new_today),
        sub: '<span class="muted">7 kunda ' + num(s.new_7d) + " · 30 kunda " + num(s.new_30d) + "</span>"
      },
      {
        cls: "kpi",
        ico: "🟢",
        label: "Faol (bugun)",
        value: num(s.active_today),
        sub: '<span class="muted">oxirgi 5 daqiqada ' + num(s.online) + " kishi</span>"
      },
      {
        cls: "kpi",
        ico: "⬇",
        label: "Yuklashlar (bugun)",
        value: num(s.downloads_today),
        sub: '<span class="muted">jami ' + num(s.downloads_total) + "</span>"
      },
      {
        cls: "kpi",
        ico: "💬",
        label: "Xabarlar (bugun)",
        value: num(s.messages_today),
        sub: '<span class="muted">jami ' + num(s.messages_total) + "</span>"
      },
      {
        cls: "kpi",
        ico: "✓",
        label: "Muvaffaqiyat",
        value: success,
        sub: '<span class="muted">video · musiqa · tavsif</span>'
      }
    ];

    $("#kpis").innerHTML = cards.map(function (c) {
      return '<article class="' + c.cls + '">' +
        '<span class="kpi-ico" aria-hidden="true">' + c.ico + "</span>" +
        '<div class="kpi-label">' + c.label + "</div>" +
        '<div class="kpi-value">' + c.value + "</div>" +
        '<div class="kpi-sub">' + c.sub + "</div>" +
        "</article>";
    }).join("");
  }

  /* ---------------- Grafiklar ---------------- */
  function chartBase() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 450 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0C1322",
          borderColor: "#1D2A45",
          borderWidth: 1,
          titleColor: "#E9EFFB",
          bodyColor: "#8B99BC",
          padding: 10,
          cornerRadius: 8,
          displayColors: true,
          boxWidth: 8,
          boxHeight: 8
        }
      },
      scales: {
        x: {
          grid: { color: "rgba(29,42,69,.45)", drawBorder: false },
          ticks: { color: "#8B99BC", font: { size: 11 }, maxRotation: 0, autoSkipPadding: 14 }
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(29,42,69,.45)", drawBorder: false },
          ticks: { color: "#8B99BC", font: { size: 11 }, precision: 0, padding: 6 }
        }
      }
    };
  }

  function renderActivity(points) {
    var ctx = document.getElementById("activityChart").getContext("2d");
    var grad = ctx.createLinearGradient(0, 0, 0, 280);
    grad.addColorStop(0, "rgba(76,201,240,.42)");
    grad.addColorStop(1, "rgba(76,201,240,0)");

    var cfg = chartBase();
    cfg.plugins.tooltip.callbacks = {
      label: function (c) {
        return " " + c.dataset.label + ": " + num(c.parsed.y);
      }
    };

    var data = {
      labels: points.map(function (p) { return p.label; }),
      datasets: [
        {
          label: "Yangi foydalanuvchi",
          data: points.map(function (p) { return p.new_users; }),
          borderColor: "#4CC9F0",
          backgroundColor: grad,
          fill: true,
          tension: 0.38,
          borderWidth: 2.2,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointBackgroundColor: "#4CC9F0"
        },
        {
          label: "Faol foydalanuvchi",
          data: points.map(function (p) { return p.active_users; }),
          borderColor: "#8B7CF6",
          backgroundColor: "transparent",
          tension: 0.38,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4
        },
        {
          label: "Yuklashlar",
          data: points.map(function (p) { return p.downloads; }),
          borderColor: "#34D399",
          backgroundColor: "transparent",
          tension: 0.38,
          borderWidth: 1.6,
          borderDash: [5, 4],
          pointRadius: 0,
          pointHoverRadius: 4
        }
      ]
    };

    if (state.charts.activity) {
      state.charts.activity.data = data;
      state.charts.activity.update();
    } else {
      state.charts.activity = new Chart(ctx, { type: "line", data: data, options: cfg });
    }
  }

  function renderHourly(points) {
    var ctx = document.getElementById("hourlyChart").getContext("2d");
    var grad = ctx.createLinearGradient(0, 0, 0, 210);
    grad.addColorStop(0, "rgba(76,201,240,.95)");
    grad.addColorStop(1, "rgba(76,201,240,.28)");

    var cfg = chartBase();
    cfg.plugins.tooltip.callbacks = {
      label: function (c) {
        return " " + c.dataset.label + ": " + num(c.parsed.y);
      }
    };
    cfg.scales.x.ticks.autoSkip = false;
    cfg.scales.x.ticks.maxRotation = 0;
    cfg.scales.x.ticks.callback = function (v, i) {
      return i % 3 === 0 ? points[i].label : "";
    };

    var data = {
      labels: points.map(function (p) { return p.label; }),
      datasets: [
        {
          type: "bar",
          label: "Hodisalar",
          data: points.map(function (p) { return p.events; }),
          backgroundColor: grad,
          borderRadius: 4,
          borderSkipped: false,
          maxBarThickness: 22,
          order: 2
        },
        {
          type: "line",
          label: "Faol foydalanuvchi",
          data: points.map(function (p) { return p.users; }),
          borderColor: "#F5A524",
          backgroundColor: "#F5A524",
          borderWidth: 1.8,
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 4,
          order: 1
        }
      ]
    };

    if (state.charts.hourly) {
      state.charts.hourly.data = data;
      state.charts.hourly.update();
    } else {
      state.charts.hourly = new Chart(ctx, { data: data, options: cfg });
    }
  }

  function renderActions(items) {
    var map = {};
    items.forEach(function (i) { map[i.type] = i; });

    var ordered = ACTION_ORDER
      .filter(function (t) { return map[t]; })
      .map(function (t) { return map[t]; });

    var total = ordered.reduce(function (a, b) { return a + b.count; }, 0);

    var data = {
      labels: ordered.map(function (i) {
        return TYPE_LABELS[i.type] || i.type;
      }),
      datasets: [{
        data: ordered.map(function (i) { return i.count; }),
        backgroundColor: ordered.map(function (i) {
          return ACTION_COLORS[i.type] || "#8B99BC";
        }),
        borderColor: "#101828",
        borderWidth: 3,
        hoverOffset: 6
      }]
    };

    var cfg = {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "66%",
      animation: { duration: 500 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0C1322",
          borderColor: "#1D2A45",
          borderWidth: 1,
          titleColor: "#E9EFFB",
          bodyColor: "#8B99BC",
          padding: 10,
          cornerRadius: 8,
          callbacks: {
            label: function (c) {
              var pct = total ? Math.round(c.parsed / total * 100) : 0;
              return " " + num(c.parsed) + " (" + pct + "%)";
            }
          }
        }
      }
    };

    if (state.charts.actions) {
      state.charts.actions.data = data;
      state.charts.actions.update();
    } else {
      state.charts.actions = new Chart(
        document.getElementById("actionsChart").getContext("2d"),
        { type: "doughnut", data: data, options: cfg }
      );
    }

    var legend = $("#actionsLegend");
    if (!ordered.length) {
      legend.innerHTML = '<li><span class="muted">Hozircha amallar yo\'q.</span></li>';
      return;
    }
    legend.innerHTML = ordered.map(function (i) {
      var pct = total ? Math.round(i.count / total * 100) : 0;
      var color = ACTION_COLORS[i.type] || "#8B99BC";
      return "<li>" +
        '<span class="swatch" style="background:' + color + '"></span>' +
        '<span class="lg-name">' + esc(TYPE_LABELS[i.type] || i.type) + "</span>" +
        '<span class="lg-val">' + num(i.count) + "</span>" +
        '<span class="lg-pct">' + pct + "%</span>" +
        "</li>";
    }).join("");
  }

  /* ---------------- Jonli hodisalar ---------------- */
  function renderFeed(items) {
    var feed = $("#feed");
    if (!items.length) {
      feed.innerHTML = '<li class="feed-empty">Hozircha hodisalar yo\'q — kimdir botga yozsa shu yerda ko\'rinadi.</li>';
      return;
    }
    feed.innerHTML = items.map(function (it) {
      var who = it.username ? "@" + it.username
        : (it.first_name || "Foydalanuvchi");
      var cls = it.ok ? "ok-" + it.type : "bad";
      var mark = it.ok ? "" : " ✕";
      return "<li>" +
        '<span class="f-time">' + fmtClock(it.ts) + "</span>" +
        '<span class="f-user">' + esc(who) + "</span>" +
        '<span class="f-type ' + cls + '">' + esc(TYPE_LABELS[it.type] || it.type) + mark + "</span>" +
        "</li>";
    }).join("");
  }

  /* ---------------- Foydalanuvchilar jadvali ---------------- */
  function renderUsers(data) {
    var body = $("#usersBody");
    var empty = $("#usersEmpty");

    $("#usersCount").textContent = data.total ? "(" + num(data.total) + ")" : "";

    if (!data.items.length) {
      body.innerHTML = "";
      empty.hidden = false;
      $("#usersEmptyText").textContent = state.search
        ? "\"" + state.search + "\" bo'yicha topilmadi."
        : "Hali ma'lumot yo'q.";
    } else {
      empty.hidden = true;
      body.innerHTML = data.items.map(function (u) {
        var name = esc(displayName(u));
        var handle = u.username ? "@" + esc(u.username) : '<span class="muted">@—</span>';
        var premium = u.is_premium ? '<span class="badge-premium">PREMIUM</span>' : "";
        return "<tr>" +
          '<td><div class="user-cell">' +
            '<span class="avatar" style="background:' + avatarColor(u.user_id) + '">' +
              esc(initials(u)) +
            "</span>" +
            "<span><span class=\"u-name\">" + name + premium + "</span><br>" +
            '<span class="u-handle">' + handle + "</span></span>" +
          "</div></td>" +
          '<td class="id-cell">' + u.user_id + "</td>" +
          '<td class="when" title="' + fmtDate(u.first_seen) + '">' + fmtDate(u.first_seen) + "</td>" +
          '<td class="when" title="' + fmtDate(u.last_seen) + '">' + relTime(u.last_seen) + "</td>" +
          '<td class="num mono">' + num(u.messages) + "</td>" +
          '<td class="num mono">' + num(u.downloads) + "</td>" +
          "</tr>";
      }).join("");
    }

    $("#pageInfo").textContent = data.page + " / " + data.pages;
    $("#prevBtn").disabled = data.page <= 1;
    $("#nextBtn").disabled = data.page >= data.pages;
  }

  /* ---------------- Ma'lumotlarni yangilash ---------------- */
  function stamp() {
    var now = new Date();
    var t = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
    $("#stamp").textContent = "yangilandi " + t;
    $("#footStamp").textContent = t;
  }

  function refreshAll() {
    var done = 0;
    var total = 5;
    var failed = 0;

    function step() {
      done += 1;
      if (done === total) {
        stamp();
        if (failed) toast("Ba'zi ma'lumotlarni olib bo'lmadi (" + failed + ").");
      }
    }

    api("/api/summary").then(renderKpis).catch(onErr).then(step);
    api("/api/timeseries?days=" + state.days)
      .then(function (d) {
        $("#tsChip").textContent = state.days + " kun";
        renderActivity(d.points);
      })
      .catch(onErr).then(step);
    api("/api/hourly?hours=24")
      .then(function (d) { renderHourly(d.points); })
      .catch(onErr).then(step);
    api("/api/actions?days=" + state.days)
      .then(function (d) { renderActions(d.items); })
      .catch(onErr).then(step);
    api("/api/events?limit=25")
      .then(function (d) { renderFeed(d.items); })
      .catch(onErr).then(step);

    function onErr(e) {
      if (e && e.status === 401) { showLogin(); return; }
      failed += 1;
    }
  }

  function refreshUsers() {
    var q = "?page=" + state.page +
      "&per_page=" + state.perPage +
      "&sort=" + encodeURIComponent(state.sort) +
      "&search=" + encodeURIComponent(state.search);
    api("/api/users" + q).then(renderUsers).catch(function (e) {
      if (e && e.status === 401) showLogin();
      else toast("Foydalanuvchilarni olib bo'lmadi.");
    });
  }

  /* ---------------- Hodisalar ---------------- */
  $("#rangeSeg").addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-days]");
    if (!btn) return;
    state.days = parseInt(btn.getAttribute("data-days"), 10);
    Array.prototype.forEach.call(this.querySelectorAll("button"), function (b) {
      b.classList.toggle("on", b === btn);
    });
    refreshAll();
  });

  $("#refreshBtn").addEventListener("click", function () {
    this.classList.add("spinning");
    var self = this;
    setTimeout(function () { self.classList.remove("spinning"); }, 320);
    refreshAll();
    refreshUsers();
  });

  $("#exportBtn").addEventListener("click", function (e) {
    e.preventDefault();
    var href = "/api/export.csv" + (state.token ? "?token=" + encodeURIComponent(state.token) : "");
    window.location.href = href;
  });

  var searchTimer = null;
  $("#searchInput").addEventListener("input", function () {
    var v = this.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.search = v.trim();
      state.page = 1;
      refreshUsers();
    }, 280);
  });

  $("#sortSelect").addEventListener("change", function () {
    state.sort = this.value;
    state.page = 1;
    refreshUsers();
  });

  $("#prevBtn").addEventListener("click", function () {
    if (state.page > 1) { state.page -= 1; refreshUsers(); }
  });

  $("#nextBtn").addEventListener("click", function () {
    state.page += 1;
    refreshUsers();
  });

  /* ---------------- Ishga tushirish ---------------- */
  function setBot(me) {
    if (me.bot && me.bot.username) {
      $("#botHandle").textContent = "@" + me.bot.username;
      document.title = "@" + me.bot.username + " · Dashboard";
    }
    if (me.db && me.db.path) {
      $("#dbInfo").textContent = me.db.path;
    }
  }

  function boot() {
    fetch("/api/me")
      .then(function (r) { return r.json(); })
      .then(function (me) {
        state.authRequired = !!me.auth_required;
        setBot(me);

        if (state.authRequired && !state.token) {
          showLogin();
          return;
        }
        // tokenni tekshiramiz
        return api("/api/summary").then(function () {
          showApp();
          refreshAll();
          refreshUsers();
          if (state.timer) clearInterval(state.timer);
          state.timer = setInterval(refreshAll, 15000);
        }).catch(function (e) {
          if (e && e.status === 401) {
            localStorage.removeItem("dash_token");
            state.token = "";
            showLogin();
          } else {
            showApp();
            toast("Serverga ulanib bo'lmadi — qayta urinilmoqda.");
            refreshAll();
          }
        });
      })
      .catch(function () {
        showApp();
        toast("Dashboard serverga ulanib bo'lmadi.");
      });
  }

  boot();
})();
