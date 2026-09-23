/* eget 购物商城 SPA —— 哈希路由 + 本地购物车 + 订单/售后对接智诉决策系统 */
(function () {
  "use strict";

  var app = document.getElementById("app");
  var modalRoot = document.getElementById("modalRoot");
  var toastRoot = document.getElementById("toastRoot");
  var cartCountEl = document.getElementById("cartCount");
  var searchInput = document.getElementById("searchInput");
  var accountArea = document.getElementById("accountArea");
  var accountText = document.getElementById("accountText");
  var logoutBtn = document.getElementById("logoutBtn");
  var assistantWidget = document.getElementById("assistantWidget");
  var assistantToggle = document.getElementById("assistantToggle");
  var assistantPanel = document.getElementById("assistantPanel");
  var assistantClose = document.getElementById("assistantClose");
  var assistantMessages = document.getElementById("assistantMessages");
  var assistantForm = document.getElementById("assistantForm");
  var assistantInput = document.getElementById("assistantInput");
  var assistantSend = document.getElementById("assistantSend");
  var assistantQuick = document.getElementById("assistantQuick");

  var PRODUCTS = [];
  var PAGE_SIZE = 24;
  var listState = { items: [], shown: 0 };
  var ordersPollTimer = null;
  var authToken = localStorage.getItem("eget_auth_token") || "";
  var currentAccount = "";
  var assistantBusy = false;

  /* ---------- 商品分类 ---------- */
  var SHOP_CATEGORIES = [
    { id: "", label: "全部" },
    { id: "mobile", label: "全部手机" },
    { id: "vivo-phone", label: "vivo 手机" },
    { id: "iqoo-phone", label: "iQOO 手机" },
    { id: "phone-accessories", label: "手机配件" },
    { id: "tablet", label: "平板电脑" },
    { id: "wearable", label: "智能穿戴" },
    { id: "computer-office", label: "电脑办公" },
    { id: "audio-video", label: "影音娱乐" },
    { id: "small-appliance", label: "小家电" },
    { id: "smart-home", label: "智能家居" },
    { id: "kitchen-dining", label: "厨房用品" },
    { id: "travel-sports", label: "出行运动" },
    { id: "car-accessories", label: "车周边" },
    { id: "cleaning-daily", label: "洗护日用" },
    { id: "children", label: "儿童用品" },
    { id: "service", label: "服务" }
  ];

  function productCategoryId(p) {
    var name = String(p.name || "");
    if (p.cat === "服务") return "service";
    if (p.cat === "智能手机") return /iQOO/i.test(name) ? "iqoo-phone" : "vivo-phone";

    /* 儿童和车载商品优先判断，避免被“手表”“支架”等通用词提前归类。 */
    if (/(儿童|小天才|玩具|益智|学生文具|积木|婴儿|毛绒公仔)/i.test(name)) return "children";
    if (/(车载|汽车|车用|行车|车充|车内|车品)/i.test(name)) return "car-accessories";
    if (/(平板电脑|\bPad\d|vivo Pad|iQOO Pad|触控笔|Pencil|平板保护|平板键盘|智能触控键盘)/i.test(name)) return "tablet";
    if (/(手表|手环|WATCH|TWS|耳机|耳麦|耳塞|头戴)/i.test(name)) return "wearable";
    if (/(手机壳|保护壳|保护膜|钢化膜|镜头膜|手机挂绳|手机支架|气囊支架|充电器|充电头|数据线|充电线|移动电源|充电宝|自拍杆|增距镜|散热背夹|手机夹)/i.test(name)) return "phone-accessories";
    if (/(鼠标|鼠标垫|键盘|键盘手托|U盘|优盘|硬盘|显示器|拓展坞|打印机|电脑包|办公)/i.test(name)) return "computer-office";
    if (/(音箱|音响|麦克风|投影|游戏手柄|游戏机|补光灯|唱歌机)/i.test(name)) return "audio-video";
    if (/(智能门锁|智能插座|魔方插座|台灯|小夜灯|氛围灯|智能摄像|路由器|体脂秤|体重秤|温湿度计)/i.test(name)) return "smart-home";
    if (/(风扇|吹风机|剃须刀|电动牙刷|冲牙器|加湿器|净化器|按摩|除螨仪|吸尘器|暖风机)/i.test(name)) return "small-appliance";
    if (/(咖啡杯|保温杯|直饮杯|水杯|吸管杯|餐具|筷|饭碗|铁锅|雪平锅|水壶|保温壶|咖啡壶|茶具|保鲜膜|保鲜盒|保鲜罩|饭盒|餐盒|砧板|菜板|搅拌盆|榨汁|破壁|烧水)/i.test(name)) return "kitchen-dining";
    if (/(雨伞|晴雨伞|背包|行李箱|旅行|户外|折叠椅|跳绳|运动|防晒|驱蚊|骑行|露营)/i.test(name)) return "travel-sports";
    if (/(洗衣液|洗衣凝珠|清洁|湿巾|纸巾|抽纸|毛巾|浴巾|收纳|香皂|洗手液|牙膏|牙线|护肤|洗护|除味|香薰)/i.test(name)) return "cleaning-daily";
    return "cleaning-daily";
  }

  function categoryLabel(id) {
    var category = SHOP_CATEGORIES.find(function (item) { return item.id === id; });
    return category ? category.label : "全部商品";
  }

  function normalizeCategoryId(id) {
    /* 兼容用户收藏或历史页面中的旧分类地址。 */
    if (id === "智能手机") return "mobile";
    if (id === "配件产品") return "";
    if (id === "服务") return "service";
    return SHOP_CATEGORIES.some(function (item) { return item.id === id; }) ? id : "";
  }

  function productMatchesCategory(p, id) {
    var productCategory = productCategoryId(p);
    if (!id) return true;
    if (id === "mobile") return productCategory === "vivo-phone" || productCategory === "iqoo-phone";
    return productCategory === id;
  }

  /* ---------- 工具 ---------- */
  function fmtPrice(n) {
    n = Number(n) || 0;
    return "¥" + (Number.isInteger(n) ? n.toLocaleString("zh-CN") : n.toFixed(2));
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toast(msg) {
    var t = document.createElement("div");
    t.className = "toast";
    t.textContent = msg;
    toastRoot.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }

  /* ---------- 第一阶段只读 RAG 知识客服 ---------- */
  function setAssistantOpen(open) {
    assistantPanel.hidden = !open;
    assistantToggle.hidden = open;
    assistantToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      if (!assistantMessages.children.length) {
        appendAssistantMessage("assistant", "你好，我是 eget 知识客服。我可以回答商品、物流、售后和退款规则，并会标注答案来源。找不到可靠答案时，我会明确告诉你“不确定”。", []);
      }
      setTimeout(function () { assistantInput.focus(); }, 60);
    }
  }

  function appendAssistantMessage(role, text, sources, meta) {
    var item = document.createElement("div");
    item.className = "assistant-message " + role + (meta && meta.uncertain ? " uncertain" : "");
    var bubble = document.createElement("div");
    bubble.className = "assistant-bubble";
    bubble.textContent = text;
    item.appendChild(bubble);
    if (sources && sources.length) {
      var refs = document.createElement("div");
      refs.className = "assistant-sources";
      var title = document.createElement("strong");
      title.textContent = "答案来源";
      refs.appendChild(title);
      sources.forEach(function (source, index) {
        var ref = document.createElement("div");
        ref.className = "assistant-source";
        ref.innerHTML = '<span>[' + (index + 1) + "]</span><div><b>" + esc(source.section || "知识库") + " · " + esc(source.title || "来源") + "</b><p>" + esc(source.snippet || "") + "</p></div>";
        refs.appendChild(ref);
      });
      item.appendChild(refs);
    }
    if (meta && meta.blocked) {
      var blocked = document.createElement("div");
      blocked.className = "assistant-safe-note";
      blocked.textContent = meta.reason === "READ_ONLY_BOUNDARY" ? "已阻止业务操作请求" : "已由安全网关拦截";
      item.appendChild(blocked);
    }
    assistantMessages.appendChild(item);
    assistantMessages.scrollTop = assistantMessages.scrollHeight;
    return item;
  }

  function askAssistant(question) {
    question = String(question || "").trim();
    if (!question || assistantBusy) return;
    assistantBusy = true;
    assistantInput.value = "";
    assistantInput.style.height = "auto";
    appendAssistantMessage("user", question, []);
    var loading = appendAssistantMessage("assistant loading", "正在检索知识库…", []);
    assistantSend.disabled = true;
    api("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
    }).then(function (result) {
      loading.remove();
      appendAssistantMessage("assistant", result.answer, result.sources || [], {
        uncertain: result.uncertain,
        blocked: result.safety && result.safety.blocked,
        reason: result.safety && result.safety.reason,
      });
    }).catch(function (error) {
      loading.remove();
      appendAssistantMessage("assistant", "知识客服暂时不可用：" + error.message, [], { uncertain: true });
    }).finally(function () {
      assistantBusy = false;
      assistantSend.disabled = false;
      assistantInput.focus();
    });
  }

  assistantToggle.onclick = function () { setAssistantOpen(true); };
  assistantClose.onclick = function () { setAssistantOpen(false); };
  assistantForm.onsubmit = function (event) { event.preventDefault(); askAssistant(assistantInput.value); };
  assistantInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      askAssistant(assistantInput.value);
    }
  });
  assistantInput.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 96) + "px";
  });
  assistantQuick.querySelectorAll("button").forEach(function (button) {
    button.onclick = function () { askAssistant(button.textContent); };
  });
  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (authToken) opts.headers.Authorization = "Bearer " + authToken;
    return fetch(path, opts).then(function (r) {
      return r.text().then(function (text) {
        var body;
        try { body = text ? JSON.parse(text) : {}; }
        catch (e) { body = { detail: text || ("请求失败 " + r.status) }; }
        if (!r.ok) {
          if (r.status === 401 && path.indexOf("/api/auth/") !== 0) signOut(false);
          throw new Error(body.detail || ("请求失败 " + r.status));
        }
        return body;
      });
    }).catch(function (error) {
      if (error instanceof TypeError || error.message === "Failed to fetch") {
        throw new Error("商城服务暂时无法连接，请刷新页面后重试");
      }
      throw error;
    });
  }

  function apiBlob(path) {
    var headers = {};
    if (authToken) headers.Authorization = "Bearer " + authToken;
    return fetch(path, { headers: headers }).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          throw new Error(body.detail || ("凭证读取失败 " + r.status));
        });
      }
      return r.blob();
    }).then(function (blob) { return URL.createObjectURL(blob); });
  }

  function maskedAccount(account) {
    return account ? account.slice(0, 3) + "****" + account.slice(-4) : "";
  }

  function cartStorageKey() {
    return currentAccount ? "eget_cart_" + currentAccount : "eget_cart_guest";
  }

  /* ---------- 购物车（localStorage） ---------- */
  function getCart() {
    try { return JSON.parse(localStorage.getItem(cartStorageKey()) || "[]"); } catch (e) { return []; }
  }
  function saveCart(cart) {
    localStorage.setItem(cartStorageKey(), JSON.stringify(cart));
    renderCartCount();
  }
  function renderCartCount() {
    var n = getCart().reduce(function (s, i) { return s + i.qty; }, 0);
    cartCountEl.hidden = n === 0;
    cartCountEl.textContent = n;
  }
  function addToCart(sku, qty, silent) {
    var p = PRODUCTS.find(function (x) { return x.sku === sku; });
    if (!p) return;
    var cart = getCart();
    var item = cart.find(function (x) { return x.sku === sku; });
    if (item) item.qty += qty;
    else cart.push({ sku: p.sku, name: p.name, brief: p.brief, price: p.price, img: p.img, qty: qty });
    saveCart(cart);
    if (!silent) toast("已加入购物车");
  }
  function flyToCart(fromEl) {
    if (!fromEl) return;
    var r = fromEl.getBoundingClientRect();
    var c = document.querySelector(".cart-link").getBoundingClientRect();
    var dot = document.createElement("div");
    dot.className = "fly-dot";
    dot.style.left = r.left + r.width / 2 + "px";
    dot.style.top = r.top + "px";
    document.body.appendChild(dot);
    requestAnimationFrame(function () {
      dot.style.left = c.left + c.width / 2 + "px";
      dot.style.top = c.top + c.height / 2 + "px";
      dot.style.transform = "scale(.3)";
      dot.style.opacity = ".4";
    });
    setTimeout(function () { dot.remove(); }, 750);
  }

  /* ---------- 商城账号 ---------- */
  function showAuthView(mode) {
    mode = mode === "register" ? "register" : "login";
    if (ordersPollTimer) clearTimeout(ordersPollTimer);
    document.body.classList.remove("booting");
    document.body.classList.add("auth-mode");
    accountArea.hidden = true;
    assistantWidget.hidden = true;
    setAssistantOpen(false);
    var isRegister = mode === "register";
    app.innerHTML =
      '<section class="auth-shell">' +
        '<div class="auth-story">' +
          '<a class="auth-brand" href="#/">eget</a>' +
          '<div class="auth-badge">EGET MEMBER</div>' +
          '<h1>好物不言，<br><span>静待知己。</span></h1>' +
          '<p>登录后即可选购商品、管理个人订单，并实时查看多 Agent 售后审核进度。</p>' +
          '<div class="auth-features"><span>✓ 正品保障</span><span>✓ 智能售后</span><span>✓ 订单隔离</span></div>' +
          '<div class="auth-orb orb-one"></div><div class="auth-orb orb-two"></div>' +
        '</div>' +
        '<div class="auth-panel">' +
          '<div class="auth-card">' +
            '<div class="auth-tabs"><button data-auth-tab="login" class="' + (!isRegister ? "active" : "") + '">登录</button><button data-auth-tab="register" class="' + (isRegister ? "active" : "") + '">注册</button></div>' +
            '<div class="auth-heading"><h2>' + (isRegister ? "创建 eget 账号" : "欢迎回来") + '</h2><p>' + (isRegister ? "注册后即可开启完整购物体验" : "登录后继续访问 eget 购物商城") + '</p></div>' +
            '<form id="authForm" novalidate>' +
              '<label class="auth-field"><span>手机号账号</span><div class="auth-input"><span>+86</span><input id="authAccount" type="tel" inputmode="numeric" autocomplete="username" maxlength="11" placeholder="请输入11位数字" required></div></label>' +
              '<label class="auth-field"><span>密码</span><div class="auth-input"><input id="authPassword" type="password" inputmode="numeric" autocomplete="' + (isRegister ? "new-password" : "current-password") + '" maxlength="32" placeholder="请输入至少6位数字" required><button type="button" id="togglePassword" aria-label="显示密码">显示</button></div></label>' +
              (isRegister ? '<label class="auth-field"><span>确认密码</span><div class="auth-input"><input id="authConfirm" type="password" inputmode="numeric" autocomplete="new-password" maxlength="32" placeholder="请再次输入密码" required></div></label>' : "") +
              '<p class="auth-error" id="authError" role="alert"></p>' +
              '<button class="auth-submit" id="authSubmit" type="submit">' + (isRegister ? "注册并进入商城" : "登录") + '</button>' +
            '</form>' +
            '<p class="auth-tip">账号须为11位数字，密码须为至少6位数字</p>' +
          '</div>' +
        '</div>' +
      '</section>';

    app.querySelectorAll("[data-auth-tab]").forEach(function (button) {
      button.onclick = function () { showAuthView(button.getAttribute("data-auth-tab")); };
    });
    document.getElementById("togglePassword").onclick = function () {
      var input = document.getElementById("authPassword");
      input.type = input.type === "password" ? "text" : "password";
      this.textContent = input.type === "password" ? "显示" : "隐藏";
    };
    document.getElementById("authForm").onsubmit = function (event) {
      event.preventDefault();
      var account = document.getElementById("authAccount").value.trim();
      var password = document.getElementById("authPassword").value.trim();
      var error = document.getElementById("authError");
      if (!/^\d{11}$/.test(account)) { error.textContent = "请输入11位数字账号"; return; }
      if (!/^\d{6,}$/.test(password)) { error.textContent = "密码必须是至少6位数字"; return; }
      if (isRegister && password !== document.getElementById("authConfirm").value.trim()) {
        error.textContent = "两次输入的密码不一致"; return;
      }
      error.textContent = "";
      var submit = document.getElementById("authSubmit");
      submit.disabled = true;
      submit.textContent = isRegister ? "正在注册…" : "正在登录…";
      api("/api/auth/" + (isRegister ? "register" : "login"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account: account, password: password }),
      }).then(function (result) {
        enterStore(result.token, result.account);
        toast(isRegister ? "注册成功，欢迎加入 eget" : "登录成功");
      }).catch(function (err) {
        error.textContent = err.message;
        submit.disabled = false;
        submit.textContent = isRegister ? "注册并进入商城" : "登录";
      });
    };
  }

  function enterStore(token, account) {
    authToken = token || authToken;
    currentAccount = account;
    localStorage.setItem("eget_auth_token", authToken);
    var legacyCart = localStorage.getItem("eget_cart");
    var accountCartKey = "eget_cart_" + account;
    if (legacyCart && !localStorage.getItem(accountCartKey)) {
      localStorage.setItem(accountCartKey, legacyCart);
      localStorage.removeItem("eget_cart");
    }
    document.body.classList.remove("booting", "auth-mode");
    accountArea.hidden = false;
    accountText.textContent = maskedAccount(account);
    assistantWidget.hidden = false;
    renderCartCount();
    loadProductsAndRoute();
  }

  function signOut(showMessage) {
    authToken = "";
    currentAccount = "";
    localStorage.removeItem("eget_auth_token");
    assistantWidget.hidden = true;
    setAssistantOpen(false);
    location.hash = "#/";
    showAuthView("login");
    if (showMessage !== false) toast("已安全退出");
  }

  function loadProductsAndRoute() {
    if (PRODUCTS.length) { route(); return; }
    app.innerHTML = '<div class="grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
    fetch("products.json").then(function (r) { return r.json(); }).then(function (data) {
      PRODUCTS = data;
      route();
    }).catch(function () {
      app.innerHTML = '<div class="empty"><strong>商品加载失败</strong><p>请刷新页面后重试</p></div>';
    });
  }

  /* ---------- 路由 ---------- */
  /* ---------- 数字人直播 ---------- */
  var liveTimers = [];
  var liveCleanups = [];
  function clearLiveTimers() {
    liveTimers.forEach(function (t) { clearInterval(t); });
    liveTimers = [];
    liveCleanups.forEach(function (fn) { try { fn(); } catch (e) {} });
    liveCleanups = [];
  }

  var LIVE_DANMAKU = [
    "这个价格真的香，已下单",
    "主播讲解好专业",
    "S50t 灵感紫也太好看了吧",
    "10000mAh 电池太顶了",
    "学生党求优惠券",
    "刚买了 Neo11，打游戏很流畅",
    "手表能独立通话吗？",
    "平板办公够用吗？",
    "直播间价格是最低的吗",
    "666，主播给力",
    "湿手秒开指纹真的假的",
    "已关注主播，每场必看",
    "TWS 耳机降噪效果怎么样",
    "顺丰包邮吗？",
    "求抽一个免单"
  ];

  function liveProductPool() {
    /* 每个直播品类挑几件商品组成本场货盘 */
    var picks = [];
    ["mobile", "tablet", "wearable", "phone-accessories"].forEach(function (catId) {
      PRODUCTS.filter(function (p) { return productMatchesCategory(p, catId); })
        .slice(0, 3)
        .forEach(function (p) { picks.push(p); });
    });
    return picks.length ? picks : PRODUCTS.slice(0, 8);
  }

  /* 自定义货盘：用户选品存 localStorage，为空则回退默认货盘 */
  var LIVE_PICK_KEY = "eget_live_picks";
  function loadLiveGoods() {
    var skus = [];
    try { skus = JSON.parse(localStorage.getItem(LIVE_PICK_KEY) || "[]"); } catch (e) { skus = []; }
    if (!Array.isArray(skus) || !skus.length) return null;
    var set = {};
    skus.forEach(function (s) { set[s] = true; });
    var goods = PRODUCTS.filter(function (p) { return set[p.sku]; });
    return goods.length ? goods : null;
  }

  function viewLive() {
    var goods = loadLiveGoods() || liveProductPool();
    if (!goods.length) {
      app.innerHTML = '<div class="empty"><strong>直播暂未开始</strong>商品加载失败，请稍后重试</div>';
      return;
    }
    var state = {
      idx: 0, viewers: 12683, likes: 8620, followed: false,
      sessionId: null, digitalHuman: null, mainVideoUrl: "", seenInteractions: {}, playedReplies: {},
      commentaryBusy: false, initialCommentaryRequested: false, activeSku: "", submittingHost: false,
      durations: {}, statusSocket: null, socketSessionId: "", rtcPc: null, rtcKey: "", rtcConnecting: false, rtcFallbackKey: "",
      queueSignature: "", queueOrderSyncing: false, previewPortraitUrl: "", autoApplyTimer: null, pendingHostApply: false
    };
    goods.forEach(function (product) { state.durations[String(product.sku)] = 300; });

    app.innerHTML =
      '<div class="live-head">' +
        "<div><h1>数字人直播间</h1><p>AI 数字人主播 · 商品讲解与弹幕实时问答</p></div>" +
        '<div class="live-head-tags"><span>DeepSeek 大脑</span><span>官方自营</span><span>正品保障</span><span>顺丰包邮</span></div>' +
      "</div>" +
      '<div class="live-layout">' +
        /* 左：直播画面 */
        '<div class="live-stage-col">' +
          '<div class="live-stage" id="liveStage">' +
            '<div class="live-stage-top">' +
              '<span class="live-badge" id="liveConnection"><i></i>连接数字人</span>' +
              '<span class="live-viewers" id="liveViewers"></span>' +
            "</div>" +
            '<div class="live-danmaku" id="liveDanmaku"></div>' +
            '<video class="live-real-video" id="liveVideo" playsinline loop preload="metadata"></video>' +
            '<a class="live-product-layer" id="liveProductLayer" hidden></a>' +
            '<div class="live-subtitle-layer" id="liveSubtitleLayer" hidden></div>' +
            '<div class="live-video-empty" id="liveVideoEmpty"><strong>AI 数字人主播</strong><span>正在连接 AutoDL 直播服务…</span></div>' +
          "</div>" +
          '<section class="live-studio-panel" aria-label="数字人直播控制台">' +
            '<div class="live-studio-head"><strong>数字人直播控制台</strong><span id="liveStudioStatus">正在连接</span></div>' +
            '<div class="live-profile-controls">' +
              '<label class="live-upload-card"><input id="livePortraitInput" type="file" accept="image/png,image/jpeg,image/webp"><b>上传数字人形象</b><span id="livePortraitName">PNG / JPG / WEBP</span></label>' +
              '<label class="live-upload-card"><input id="liveVoiceInput" type="file" accept="audio/*"><b>上传克隆声音</b><span id="liveVoiceName">建议 5–15 秒清晰录音</span></label>' +
              '<label class="live-upload-card"><input id="liveProductVideoInput" type="file" accept="video/*"><b>商品特写视频（可选）</b><span id="liveProductVideoName">用于队列首件商品特写</span></label>' +
              '<input class="live-reference-text" id="liveReferenceText" maxlength="200" placeholder="录音文字（可选，需与录音内容一致）">' +
              '<select class="live-expression-mode" id="liveExpressionMode" aria-label="表情模式"><option value="normal">正常表情</option><option value="funny">搞怪表情与动作</option></select>' +
              '<select class="live-expression-mode" id="liveSceneTemplate" aria-label="直播场景"><option value="3c">3C直播间</option><option value="food">食品直播间</option><option value="beauty">美妆直播间</option><option value="fashion">服装直播间</option><option value="general">通用直播间</option></select>' +
              '<select class="live-expression-mode" id="liveOrientation" aria-label="画面方向"><option value="landscape">横屏 16:9</option><option value="portrait">竖屏 9:16</option></select>' +
              '<button class="live-apply-host" id="liveApplyHostBtn" type="button">应用形象/声音并重新生成</button>' +
            "</div>" +
            '<div class="live-playback-controls">' +
              '<button id="liveStartBtn" type="button">开始直播</button>' +
              '<button id="livePauseBtn" type="button">暂停</button>' +
              '<button id="liveResumeBtn" type="button">恢复</button>' +
              '<button id="liveStopBtn" class="danger" type="button">停止</button>' +
            "</div>" +
          "</section>" +
          '<div class="live-now live-now-external" id="liveNow"></div>' +
          '<div class="live-ops">' +
            '<button class="live-op" id="liveLikeBtn" type="button">❤ <b id="liveLikes"></b></button>' +
            '<button class="live-op" id="liveFollowBtn" type="button"></button>' +
            '<button class="live-op" id="liveShareBtn" type="button">↗ 分享</button>' +
          "</div>" +
          '<form class="live-danmaku-form" id="liveDanmakuForm">' +
            '<input id="liveDanmakuInput" maxlength="24" placeholder="发条短弹幕，问问数字人…" autocomplete="off">' +
            '<button type="submit">发送</button>' +
          "</form>" +
        "</div>" +
        /* 右：本场直播商品 */
        '<div class="live-goods-col">' +
          '<div class="live-goods-head"><h2>本场直播商品</h2><div class="live-goods-head-right"><span id="liveGoodsCount"></span><button id="livePickBtn" type="button">选品</button></div></div>' +
          '<div class="live-goods-list" id="liveGoodsList"></div>' +
        "</div>" +
      "</div>";

    var liveVideo = document.getElementById("liveVideo");
    var liveVideoEmpty = document.getElementById("liveVideoEmpty");
    var liveProductLayer = document.getElementById("liveProductLayer");
    var liveSubtitleLayer = document.getElementById("liveSubtitleLayer");
    var liveConnection = document.getElementById("liveConnection");
    var liveStudioStatus = document.getElementById("liveStudioStatus");
    var livePortraitInput = document.getElementById("livePortraitInput");
    var liveVoiceInput = document.getElementById("liveVoiceInput");
    var liveProductVideoInput = document.getElementById("liveProductVideoInput");
    var liveApplyHostBtn = document.getElementById("liveApplyHostBtn");
    var liveStartBtn = document.getElementById("liveStartBtn");
    var livePauseBtn = document.getElementById("livePauseBtn");
    var liveResumeBtn = document.getElementById("liveResumeBtn");
    var liveStopBtn = document.getElementById("liveStopBtn");

    function updateStudioControls(session) {
      var ready = Boolean(session && session.render_status === "ready");
      var generating = Boolean(session && (session.render_status === "queued" || session.render_status === "rendering"));
      var playback = session ? session.playback : "";
      liveApplyHostBtn.disabled = state.submittingHost || generating;
      liveApplyHostBtn.textContent = state.submittingHost ? "正在提交…" : generating ? "数字人生成中…" : "生成 V1 商品队列";
      liveStartBtn.disabled = !ready || playback === "playing";
      livePauseBtn.disabled = !ready || playback !== "playing";
      liveResumeBtn.disabled = !ready || playback !== "paused";
      liveStopBtn.disabled = !ready || playback === "stopped";
      var action = session && session.current_action ? " · " + (session.current_action.label || session.current_action.action) : "";
      var remaining = session && typeof session.remaining_seconds === "number" ? " · " + session.remaining_seconds + "秒后切换" : "";
      liveStudioStatus.textContent = state.submittingHost ? "正在上传" : generating ? "生成中 " + (session.progress || 0) + "%" : playback === "playing" ? "直播中" + action + remaining : playback === "paused" ? "已暂停" + action : ready ? "画面已就绪" : "等待连接";
    }

    function closeWebRTC() {
      if (state.rtcPc) {
        try { state.rtcPc.close(); } catch (e) {}
      }
      state.rtcPc = null;
      state.rtcKey = "";
      state.rtcConnecting = false;
      if (liveVideo.srcObject) liveVideo.srcObject = null;
    }

    function connectWebRTC(session) {
      if (!window.RTCPeerConnection || !session || !session.transport || !session.transport.webrtc_available || session.playback !== "playing" || !session.video_url) return;
      var key = session.id + ":" + String(session.queue_index || 0);
      // SSH tunnels carry the HTTP MP4 fallback, but normally cannot carry
      // WebRTC's UDP media path.  Once negotiation fails for this queue item,
      // do not retry on every status update: each retry used to reset the MP4
      // to time 0 after nine seconds, so viewers only heard the first sentence.
      if (state.rtcFallbackKey === key) {
        liveConnection.innerHTML = "<i></i>LIVE · MP4";
        return;
      }
      if ((state.rtcPc || state.rtcConnecting) && state.rtcKey === key) return;
      closeWebRTC();
      // Keep the HTTP MP4 visible while ICE/WebRTC is negotiating.  An SSH
      // tunnel forwards HTTP but usually cannot carry the peer UDP path.
      // Switching srcObject in ontrack (before ICE is connected) therefore
      // replaced a healthy MP4 fallback with a permanently black frame.
      if (liveVideo.dataset.mode === "webrtc") restoreMainVideo();
      state.rtcKey = key;
      state.rtcConnecting = true;
      var pc = new RTCPeerConnection();
      state.rtcPc = pc;
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });
      var stream = new MediaStream();

      function activateWebRTCIfReady() {
        if (state.rtcKey !== key || state.rtcPc !== pc) return;
        if (pc.connectionState !== "connected" || stream.getVideoTracks().length === 0) return;
        state.rtcConnecting = false;
        liveVideo.dataset.mode = "webrtc";
        liveVideo.srcObject = stream;
        liveVideo.play().catch(function () {});
        liveConnection.innerHTML = "<i></i>LIVE · WebRTC";
      }

      function fallBackToMp4() {
        if (state.rtcKey !== key) return;
        var wasUsingWebRTC = liveVideo.dataset.mode === "webrtc" || Boolean(liveVideo.srcObject);
        state.rtcFallbackKey = key;
        closeWebRTC();
        if (wasUsingWebRTC) restoreMainVideo();
        else {
          // MP4 was already playing while negotiation ran.  Keep its current
          // time instead of assigning src again and restarting the product.
          liveVideo.dataset.mode = "main";
          liveConnection.innerHTML = "<i></i>LIVE · MP4";
        }
      }

      pc.ontrack = function (event) {
        stream.addTrack(event.track);
        activateWebRTCIfReady();
      };
      pc.onconnectionstatechange = function () {
        if (pc.connectionState === "connected") activateWebRTCIfReady();
        if (["failed", "disconnected", "closed"].indexOf(pc.connectionState) >= 0) fallBackToMp4();
      };
      pc.oniceconnectionstatechange = function () {
        if (["failed", "disconnected", "closed"].indexOf(pc.iceConnectionState) >= 0) fallBackToMp4();
      };
      pc.createOffer().then(function (offer) {
        return pc.setLocalDescription(offer).then(function () { return offer; });
      }).then(function (offer) {
        return api(session.transport.webrtc_offer, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sdp: offer.sdp, type: offer.type })
        });
      }).then(function (answer) {
        if (state.rtcKey !== key) return;
        return pc.setRemoteDescription(answer);
      }).catch(function () {
        fallBackToMp4();
      });
      setTimeout(function () {
        if (state.rtcKey === key && (pc.connectionState !== "connected" || stream.getVideoTracks().length === 0)) fallBackToMp4();
      }, 9000);
    }

    function connectStatusSocket(session) {
      if (!session || String(session.version || "").indexOf("V1") !== 0 || state.socketSessionId === session.id) return;
      if (state.statusSocket) {
        try { state.statusSocket.close(); } catch (e) {}
      }
      state.socketSessionId = session.id;
      fetch("/api/digital-human/config").then(function (response) { return response.json(); }).then(function (config) {
        if (state.socketSessionId !== session.id) return;
        var socket = new WebSocket(config.websocket_base + "/ws/v1/live/" + session.id);
        state.statusSocket = socket;
        socket.onmessage = function (event) {
          try {
            var payload = JSON.parse(event.data);
            if (!payload.error && payload.id === state.sessionId) renderDigitalHuman(payload);
          } catch (e) {}
        };
        socket.onclose = function () {
          if (state.statusSocket === socket) state.statusSocket = null;
          if (state.socketSessionId === session.id) {
            state.socketSessionId = "";
            setTimeout(function () {
              if (state.sessionId === session.id) connectStatusSocket(state.digitalHuman);
            }, 2000);
          }
        };
        socket.onerror = function () { try { socket.close(); } catch (e) {} };
      }).catch(function () {});
    }

    liveCleanups.push(function () {
      closeWebRTC();
      if (state.autoApplyTimer) clearTimeout(state.autoApplyTimer);
      if (state.previewPortraitUrl) URL.revokeObjectURL(state.previewPortraitUrl);
      if (state.statusSocket) {
        try { state.statusSocket.close(); } catch (e) {}
      }
    });

    function restoreMainVideo() {
      if (!state.mainVideoUrl) return;
      var sourceChanged = liveVideo.dataset.mode !== "main" || liveVideo.dataset.mainUrl !== state.mainVideoUrl;
      if (liveVideo.srcObject) liveVideo.srcObject = null;
      // The backend owns the 300-second product rotation.  Do not loop a
      // product locally; hold the last frame until the next queue URL arrives.
      liveVideo.loop = false;
      liveVideo.dataset.mode = "main";
      if (sourceChanged) {
        liveVideo.dataset.mainUrl = state.mainVideoUrl;
        liveVideo.src = state.mainVideoUrl;
      }
      if (!state.digitalHuman || state.digitalHuman.playback === "playing") {
        liveVideo.play().catch(function () {});
        liveConnection.innerHTML = "<i></i>LIVE · MP4";
      }
    }

    liveVideo.onended = function () {
      if (liveVideo.dataset.mode === "reply") {
        restoreMainVideo();
        connectWebRTC(state.digitalHuman);
      } else if (liveVideo.dataset.mode === "main") {
        pollDigitalHuman();
      }
    };

    function syncLiveQueueOrder(session) {
      if (!session || session.acceptance_sample || String(session.version || "").indexOf("V1") !== 0 || state.queueOrderSyncing) return;
      var desired = goods.map(function (product) { return String(product.sku || ""); }).filter(Boolean);
      var queue = session.queue || [];
      var actual = queue.map(function (item) {
        return String(item.product && item.product.sku || "");
      }).filter(Boolean);
      if (!desired.length || (desired.length === actual.length && desired.join("|") === actual.join("|"))) return;
      state.queueOrderSyncing = true;
      api("/api/v1/live/" + session.id + "/queue/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skus: desired })
      }).then(renderDigitalHuman).catch(function (error) {
        toast("商品顺序同步失败：" + error.message);
      }).finally(function () {
        state.queueOrderSyncing = false;
      });
    }

    function activeQueueItem(session) {
      if (!session || !session.queue || !session.queue.length) return null;
      var sku = session.active_product && String(session.active_product.sku || "");
      return session.queue.find(function (item) {
        return sku && item.product && String(item.product.sku || "") === sku;
      }) || session.queue[Math.max(0, Number(session.queue_index || 0))] || null;
    }

    function splitSubtitle(text) {
      var parts = String(text || "").match(/[^，。！？；,.!?;]+[，。！？；,.!?;]?/g) || [];
      var lines = [], current = "";
      parts.forEach(function (part) {
        if (current && current.length + part.length > 24) {
          lines.push(current);
          current = part;
        } else current += part;
      });
      if (current) lines.push(current);
      return lines.length ? lines : [String(text || "")];
    }

    function updateLayeredSubtitle() {
      var item = activeQueueItem(state.digitalHuman);
      if (!item || item.video_layout !== "layered" || liveVideo.dataset.mode !== "main") {
        liveSubtitleLayer.hidden = true;
        return;
      }
      var time = Number(liveVideo.currentTime || 0);
      var plan = item.script_plan || [];
      var segment = plan.find(function (entry) {
        return time >= Number(entry.start || 0) && time < Number(entry.end || 0);
      });
      if (!segment || !segment.text) {
        liveSubtitleLayer.hidden = true;
        return;
      }
      var lines = splitSubtitle(segment.text);
      var start = Number(segment.start || 0), end = Math.max(start + .1, Number(segment.end || start + 1));
      var index = Math.min(lines.length - 1, Math.max(0, Math.floor(((time - start) / (end - start)) * lines.length)));
      liveSubtitleLayer.textContent = lines[index];
      liveSubtitleLayer.hidden = false;
    }

    function renderStageLayers(session) {
      var item = activeQueueItem(session);
      var layered = Boolean(item && item.video_layout === "layered");
      liveProductLayer.hidden = !layered;
      liveSubtitleLayer.hidden = !layered;
      document.getElementById("liveStage").classList.toggle("is-layered", layered);
      if (!layered) return;
      var product = item.product || currentGoods();
      var uiProduct = goods.find(function (entry) { return String(entry.sku) === String(product.sku || ""); }) || currentGoods();
      liveProductLayer.href = "#/product/" + encodeURIComponent(product.sku || uiProduct.sku || "");
      liveProductLayer.innerHTML =
        '<span class="live-product-layer-badge">正在讲解</span>' +
        '<img src="' + esc(uiProduct.img || "") + '" alt="">' +
        '<strong>' + esc(product.name || uiProduct.name || "") + '</strong>' +
        '<b>' + fmtPrice(product.sale_price || uiProduct.price || "") + '</b>' +
        '<span class="live-product-layer-buy">立即抢购</span>';
      updateLayeredSubtitle();
    }

    function renderDigitalHuman(session) {
      state.digitalHuman = session;
      state.sessionId = session.id;
      updateStudioControls(session);
      connectStatusSocket(session);
      syncLiveQueueOrder(session);
      renderStageLayers(session);
      (session.queue || []).forEach(function (item) {
        if (item.product && item.product.sku) state.durations[String(item.product.sku)] = Number(item.duration_seconds || 300);
      });
      var queueSignature = (session.queue || []).map(function (item) { return item.id + ":" + item.render_status; }).join("|");
      if (queueSignature && queueSignature !== state.queueSignature) {
        state.queueSignature = queueSignature;
        renderGoods();
      }
      if (session.active_product && session.active_product.sku && String(session.active_product.sku) !== state.activeSku) {
        var activeIndex = goods.findIndex(function (p) {
          return String(p.sku) === String(session.active_product.sku);
        });
        if (activeIndex >= 0) {
          state.idx = activeIndex;
          renderGoods();
          renderNow();
        }
        state.activeSku = String(session.active_product.sku);
      }
      if (session.render_status === "ready") {
        liveConnection.innerHTML = session.playback === "playing" ? "<i></i>LIVE 直播中" : session.playback === "paused" ? "直播已暂停" : "直播画面已就绪";
        liveVideoEmpty.hidden = true;
        var mainUrl = session.video_url;
        if (state.mainVideoUrl !== mainUrl) {
          closeWebRTC();
          state.mainVideoUrl = mainUrl;
          if (liveVideo.dataset.mode !== "reply") restoreMainVideo();
        }
        if (String(session.version || "").indexOf("V1") === 0) connectWebRTC(session);
      } else {
        liveConnection.textContent = "数字人生成中 " + (session.progress || 0) + "%";
        liveVideoEmpty.querySelector("span").textContent = session.message || "正在生成直播画面";
      }
      if (session.playback === "paused") liveVideo.pause();
      if (session.playback === "stopped") {
        liveVideo.pause();
        if (liveVideo.readyState) liveVideo.currentTime = 0;
      }
      var pending = (session.interactions || []).find(function (item) {
        return item.status === "queued" || item.status === "thinking" || item.status === "speaking";
      });
      if (pending) {
        liveConnection.textContent = pending.status === "speaking" ? "正在生成数字人语音画面" : "LLM 正在思考";
      }
      (session.interactions || []).forEach(function (item) {
        if (!state.seenInteractions[item.id + "q"]) {
          state.seenInteractions[item.id + "q"] = true;
          pushDanmaku((item.user || "观众") + "：" + item.question, item.type === "commentary");
        }
        if (item.answer && !state.seenInteractions[item.id + "a"]) {
          state.seenInteractions[item.id + "a"] = true;
          pushDanmaku("数字人：" + item.answer, true);
        }
        if (item.status === "ready" && item.video_url && !state.playedReplies[item.id]) {
          state.playedReplies[item.id] = true;
          closeWebRTC();
          liveVideo.pause();
          liveVideo.loop = false;
          liveVideo.dataset.mode = "reply";
          liveVideo.src = item.video_url + "?v=" + Date.now();
          liveVideo.play().catch(function () {});
        }
      });
      state.commentaryBusy = Boolean(pending);
      if (String(session.version || "").indexOf("V1") !== 0 && session.render_status === "ready" && !state.initialCommentaryRequested) {
        state.initialCommentaryRequested = true;
        if (!session.active_product || String(session.active_product.sku || "") !== String(currentGoods().sku)) {
          requestProductCommentary(currentGoods());
        }
      }
    }

    liveVideo.addEventListener("timeupdate", updateLayeredSubtitle);
    liveVideo.addEventListener("seeked", updateLayeredSubtitle);

    function pollDigitalHuman() {
      if (state.submittingHost && !state.sessionId) return;
      if (!state.sessionId) {
        api("/api/v1/live/current").then(renderDigitalHuman).catch(function () {
          return api("/api/v0/live/current").then(renderDigitalHuman);
        }).catch(function (error) {
          liveConnection.textContent = "数字人未连接";
          liveVideoEmpty.hidden = false;
          liveVideoEmpty.querySelector("span").textContent = error.message;
        });
        return;
      }
      var prefix = state.digitalHuman && String(state.digitalHuman.version || "").indexOf("V1") === 0 ? "/api/v1" : "/api/v0";
      api(prefix + "/live/" + state.sessionId).then(renderDigitalHuman).catch(function (error) {
        liveConnection.textContent = "数字人未连接";
        liveVideoEmpty.hidden = false;
        liveVideoEmpty.querySelector("span").textContent = error.message;
      });
    }

    function currentGoods() { return goods[state.idx]; }

    function controlLive(action) {
      if (!state.sessionId || !state.digitalHuman || state.digitalHuman.render_status !== "ready") {
        toast("请先等待数字人直播画面生成完成");
        return;
      }
      var prefix = String(state.digitalHuman.version || "").indexOf("V1") === 0 ? "/api/v1" : "/api/v0";
      api(prefix + "/live/" + state.sessionId + "/" + action, { method: "POST" }).then(function (session) {
        renderDigitalHuman(session);
        if (action === "start" || action === "resume") liveVideo.play().catch(function () {});
        if (action === "pause") liveVideo.pause();
        if (action === "stop") {
          closeWebRTC();
          liveVideo.pause();
          if (liveVideo.readyState) liveVideo.currentTime = 0;
        }
        toast({ start: "直播已开始", pause: "直播已暂停", resume: "直播已恢复", stop: "直播已停止" }[action]);
      }).catch(function (error) { toast(error.message); });
    }

    function imageAsDataUrl(url) {
      return fetch(url).then(function (response) {
        if (!response.ok) throw new Error("商品图片读取失败");
        return response.blob();
      }).then(function (blob) {
        return new Promise(function (resolve, reject) {
          var reader = new FileReader();
          reader.onload = function () { resolve(reader.result); };
          reader.onerror = function () { reject(new Error("商品图片转换失败")); };
          reader.readAsDataURL(blob);
        });
      });
    }

    function createHostSession() {
      if (state.submittingHost) {
        state.pendingHostApply = true;
        return;
      }
      if (state.autoApplyTimer) {
        clearTimeout(state.autoApplyTimer);
        state.autoApplyTimer = null;
      }
      var portrait = livePortraitInput.files[0];
      var voice = liveVoiceInput.files[0];
      var productVideo = liveProductVideoInput.files[0];
      state.submittingHost = true;
      state.sessionId = null;
      state.digitalHuman = null;
      state.mainVideoUrl = "";
      state.seenInteractions = {};
      state.playedReplies = {};
      state.initialCommentaryRequested = false;
      state.commentaryBusy = false;
      closeWebRTC();
      if (state.statusSocket) {
        try { state.statusSocket.close(); } catch (e) {}
      }
      state.socketSessionId = "";
      liveVideo.pause();
      liveVideo.removeAttribute("src");
      liveVideo.load();
      liveVideoEmpty.hidden = false;
      liveVideoEmpty.querySelector("span").textContent = "正在整理商品队列与图片…";
      updateStudioControls(null);

      Promise.all(goods.map(function (product) {
        return imageAsDataUrl(product.img).catch(function () { return ""; }).then(function (imageData) {
          return {
            sku: String(product.sku || ""),
            name: product.name || "商城直播商品",
            link: location.origin + location.pathname + "#/product/" + product.sku,
            image_data: imageData,
            original_price: String(product.market || product.price || ""),
            sale_price: String(product.price || ""),
            selling_points: product.brief || "商城精选商品",
            params: product.cat || "",
            promotion: Number(product.market) > Number(product.price) ? "商城直播优惠价，以页面显示为准" : "商城直播价，以页面显示为准",
            duration_seconds: Number(state.durations[String(product.sku)] || 300)
          };
        });
      })).then(function (products) {
        var data = new FormData();
        // Product images make this JSON larger than Starlette's 1 MB limit
        // for plain multipart fields.  A file part is streamed and preserves
        // the same payload without dropping the portrait or voice uploads.
        data.append("products_file", new Blob([JSON.stringify(products)], { type: "application/json" }), "products.json");
        data.append("reference_text", document.getElementById("liveReferenceText").value.trim());
        data.append("expression_mode", document.getElementById("liveExpressionMode").value);
        data.append("scene_template", document.getElementById("liveSceneTemplate").value);
        data.append("orientation", document.getElementById("liveOrientation").value);
        data.append("auto_rotate", "true");
        if (portrait) data.append("portrait_image", portrait, portrait.name);
        if (voice) data.append("reference_audio", voice, voice.name);
        if (productVideo) data.append("product_video", productVideo, productVideo.name);
        liveVideoEmpty.querySelector("span").textContent = "正在上传主播与商品队列…";
        return api("/api/v1/live/create", { method: "POST", body: data });
      }).then(function (session) {
        state.submittingHost = false;
        renderDigitalHuman(session);
        toast(session.custom_portrait ? "新数字人形象已提交，正在生成直播画面" : "V1 商品队列已提交，首件商品生成后即可开始直播");
        pollDigitalHuman();
        if (state.pendingHostApply) {
          state.pendingHostApply = false;
          scheduleHostAutoApply("正在应用最新的形象/声音…");
        }
      }).catch(function (error) {
        state.submittingHost = false;
        updateStudioControls(null);
        liveVideoEmpty.querySelector("span").textContent = error.message;
        toast(error.message);
      });
    }

    function requestProductCommentary(product) {
      if (!state.sessionId || !state.digitalHuman || state.digitalHuman.render_status !== "ready") {
        toast("数字人直播尚未准备完成");
        return;
      }
      if (state.commentaryBusy) {
        toast("数字人正在生成上一段讲解或回答，请稍后切换商品");
        return;
      }
      state.commentaryBusy = true;
      state.activeSku = String(product.sku || "");
      liveConnection.textContent = "正在准备商品讲解";
      pushDanmaku("数字人正在准备讲解 " + product.name, true);
      api("/api/v0/live/" + state.sessionId + "/commentary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sku: String(product.sku || ""),
          name: product.name,
          original_price: String(product.market || product.price || ""),
          sale_price: String(product.price || ""),
          selling_points: product.brief || "",
          params: product.cat || "",
          promotion: Number(product.market) > Number(product.price) ? "商城直播优惠价，以页面显示为准" : "商城直播价，以页面显示为准"
        })
      }).then(function () {
        toast("DeepSeek 正在生成商品讲解");
        pollDigitalHuman();
      }).catch(function (error) {
        state.commentaryBusy = false;
        toast(error.message);
      });
    }

    function renderNow() {
      var p = currentGoods();
      document.getElementById("liveNow").innerHTML =
        '<img src="' + p.img + '" alt="">' +
        '<div class="live-now-info">' +
          '<div class="live-now-tag">正在讲解 · ' + (state.idx + 1) + " 号链接</div>" +
          '<div class="live-now-name">' + esc(p.name) + "</div>" +
          '<div class="live-now-price">' + fmtPrice(p.price) +
            (p.market > p.price ? "<s>" + fmtPrice(p.market) + "</s>" : "") +
          "</div>" +
        "</div>" +
        '<a class="live-now-buy" href="#/product/' + p.sku + '">立即抢购</a>';
    }

    function renderGoods() {
      document.getElementById("liveGoodsCount").textContent = "共 " + goods.length + " 件";
      document.getElementById("liveGoodsList").innerHTML = goods.map(function (p, i) {
        var queueItem = state.digitalHuman && state.digitalHuman.queue ? state.digitalHuman.queue.find(function (item) {
          return item.product && String(item.product.sku) === String(p.sku);
        }) : null;
        var queueStatus = queueItem ? (queueItem.render_status === "ready" ? "已就绪" : queueItem.render_status === "failed" ? "失败" : "生成中") : "";
        return (
          '<div class="live-goods-item' + (i === state.idx ? " active" : "") + '" data-live-idx="' + i + '">' +
            '<span class="live-goods-no">' + (i + 1) + "</span>" +
            '<img src="' + p.img + '" alt="">' +
            '<div class="live-goods-info">' +
              '<div class="live-goods-name">' + esc(p.name) + "</div>" +
              '<div class="live-goods-price">' + fmtPrice(p.price) +
                (p.market > p.price ? "<s>" + fmtPrice(p.market) + "</s>" : "") +
              "</div>" +
              '<label class="live-goods-duration">讲解 <input type="number" min="15" max="3600" step="15" data-duration-sku="' + p.sku + '" value="' + Number(state.durations[String(p.sku)] || 300) + '"> 秒</label>' +
            "</div>" +
            (i === state.idx ? '<span class="live-goods-on">讲解中</span>' : queueStatus ? '<span class="live-goods-state">' + queueStatus + "</span>" : "") +
          "</div>"
        );
      }).join("");
      document.querySelectorAll("[data-duration-sku]").forEach(function (input) {
        input.onclick = function (event) { event.stopPropagation(); };
        input.onchange = function (event) {
          event.stopPropagation();
          var value = Math.max(15, Math.min(3600, Number(input.value) || 300));
          state.durations[String(input.getAttribute("data-duration-sku"))] = value;
          input.value = value;
        };
      });
      document.querySelectorAll(".live-goods-item").forEach(function (el) {
        el.onclick = function (event) {
          if (event.target.closest(".live-goods-duration")) return;
          if (state.commentaryBusy) {
            toast("数字人正在生成上一段讲解或回答，请稍后切换商品");
            return;
          }
          state.idx = Number(el.getAttribute("data-live-idx")) || 0;
          renderGoods();
          renderNow();
          pushDanmaku("主播开始讲解 " + currentGoods().name.split(" ")[0] + " 啦");
          if (state.digitalHuman && String(state.digitalHuman.version || "").indexOf("V1") === 0) {
            var queueIndex = (state.digitalHuman.queue || []).findIndex(function (item) {
              return item.product && String(item.product.sku) === String(currentGoods().sku);
            });
            if (queueIndex < 0) {
              toast("该商品不在当前 V1 队列中，请重新生成商品队列");
              return;
            }
            api("/api/v1/live/" + state.sessionId + "/product", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ index: queueIndex })
            }).then(renderDigitalHuman).catch(function (error) { toast(error.message); });
          } else {
            requestProductCommentary(currentGoods());
          }
        };
      });
    }

    function renderStats() {
      document.getElementById("liveViewers").textContent =
        "👀 " + state.viewers.toLocaleString("zh-CN") + " 人在线";
      document.getElementById("liveLikes").textContent = state.likes.toLocaleString("zh-CN");
      document.getElementById("liveFollowBtn").textContent = state.followed ? "✓ 已关注" : "+ 关注";
      document.getElementById("liveFollowBtn").classList.toggle("done", state.followed);
    }

    function pushDanmaku(text, fromHost) {
      var box = document.getElementById("liveDanmaku");
      if (!box) return;
      var fullText = String(text || "").replace(/\s+/g, " ").trim();
      var chars = Array.from(fullText);
      var shortText = chars.length > 18 ? chars.slice(0, 18).join("") + "…" : fullText;
      var d = document.createElement("div");
      d.className = "live-danmaku-item" + (fromHost ? " host" : "");
      d.textContent = shortText;
      d.title = fullText;
      box.appendChild(d);
      while (box.children.length > 4) box.removeChild(box.firstChild);
    }

    /* 选品弹窗：勾选本场要直播的商品，保存到 localStorage */
    function openLivePicker() {
      var checked = {};
      goods.forEach(function (p) { checked[p.sku] = true; });
      var filter = { cat: "", kw: "" };
      var LIVE_PICK_CATS = [
        ["", "全部"], ["mobile", "手机"], ["tablet", "平板"],
        ["wearable", "智能穿戴"], ["phone-accessories", "配件"]
      ];

      modalRoot.innerHTML =
        '<div class="modal-mask" id="livePickMask"><div class="modal live-picker-modal">' +
          '<div class="live-picker-head"><h3>编辑直播货盘</h3><button id="livePickClose" type="button" aria-label="关闭">×</button></div>' +
          '<div class="live-picker-tools">' +
            '<div class="live-picker-chips">' +
              LIVE_PICK_CATS.map(function (c, i) {
                return '<button type="button" class="chip' + (i === 0 ? " active" : "") + '" data-cat="' + c[0] + '">' + c[1] + "</button>";
              }).join("") +
            "</div>" +
            '<input id="livePickSearch" type="search" placeholder="搜索商品名" autocomplete="off">' +
          "</div>" +
          '<div class="live-picker-list" id="livePickList"></div>' +
          '<div class="live-picker-foot">' +
            '<button class="live-picker-reset" id="livePickReset" type="button">恢复默认货盘</button>' +
            '<span class="live-picker-count" id="livePickCount"></span>' +
            '<button class="btn ghost" id="livePickCancel" type="button">取消</button>' +
            '<button class="btn primary" id="livePickSave" type="button">保存货盘</button>' +
          "</div>" +
        "</div></div>";

      var list = document.getElementById("livePickList");

      function pickedCount() {
        return Object.keys(checked).filter(function (k) { return checked[k]; }).length;
      }
      function updateCount() {
        document.getElementById("livePickCount").textContent = "已选 " + pickedCount() + " 件";
      }
      function renderList() {
        var items = PRODUCTS.filter(function (p) {
          if (filter.cat && !productMatchesCategory(p, filter.cat)) return false;
          if (filter.kw && String(p.name).toLowerCase().indexOf(filter.kw) < 0) return false;
          return true;
        });
        list.innerHTML = items.slice(0, 200).map(function (p) {
          return (
            '<label class="live-picker-item' + (checked[p.sku] ? " on" : "") + '" data-sku="' + p.sku + '">' +
              '<input type="checkbox"' + (checked[p.sku] ? " checked" : "") + ">" +
              '<img src="' + p.img + '" alt="" loading="lazy">' +
              '<span class="live-picker-item-name">' + esc(p.name) + "</span>" +
              '<span class="live-picker-item-price">' + fmtPrice(p.price) + "</span>" +
            "</label>"
          );
        }).join("") || '<div class="live-picker-empty">没有匹配的商品</div>';
        updateCount();
      }

      list.onchange = function (event) {
        var item = event.target.closest(".live-picker-item");
        if (!item) return;
        var sku = item.getAttribute("data-sku");
        checked[sku] = event.target.checked;
        item.classList.toggle("on", event.target.checked);
        updateCount();
      };
      modalRoot.querySelectorAll(".live-picker-chips .chip").forEach(function (chip) {
        chip.onclick = function () {
          modalRoot.querySelectorAll(".live-picker-chips .chip").forEach(function (x) { x.classList.remove("active"); });
          chip.classList.add("active");
          filter.cat = chip.getAttribute("data-cat");
          renderList();
        };
      });
      document.getElementById("livePickSearch").oninput = function (event) {
        filter.kw = event.target.value.trim().toLowerCase();
        renderList();
      };

      function close() { modalRoot.innerHTML = ""; }
      document.getElementById("livePickClose").onclick = close;
      document.getElementById("livePickCancel").onclick = close;
      document.getElementById("livePickMask").onclick = function (event) {
        if (event.target.id === "livePickMask") close();
      };
      document.getElementById("livePickReset").onclick = function () {
        localStorage.removeItem(LIVE_PICK_KEY);
        close();
        clearLiveTimers();
        viewLive();
        toast("已恢复默认货盘");
      };
      document.getElementById("livePickSave").onclick = function () {
        var skus = PRODUCTS.filter(function (p) { return checked[p.sku]; }).map(function (p) { return p.sku; });
        if (!skus.length) { toast("至少选择 1 件商品"); return; }
        localStorage.setItem(LIVE_PICK_KEY, JSON.stringify(skus));
        close();
        clearLiveTimers();
        viewLive();
        toast("货盘已保存，本场直播商品已更新");
      };

      renderList();
    }

    document.getElementById("livePickBtn").onclick = openLivePicker;

    function scheduleHostAutoApply(message, delay) {
      if (state.autoApplyTimer) clearTimeout(state.autoApplyTimer);
      liveStudioStatus.textContent = message;
      state.autoApplyTimer = setTimeout(function () {
        state.autoApplyTimer = null;
        createHostSession();
      }, delay || 700);
    }

    livePortraitInput.onchange = function () {
      var file = livePortraitInput.files[0];
      document.getElementById("livePortraitName").textContent = file ? file.name + " · 正在应用" : "PNG / JPG / WEBP";
      if (!file) return;
      if (state.previewPortraitUrl) URL.revokeObjectURL(state.previewPortraitUrl);
      state.previewPortraitUrl = URL.createObjectURL(file);
      liveVideo.pause();
      liveVideo.poster = state.previewPortraitUrl;
      liveVideo.removeAttribute("src");
      liveVideo.load();
      liveVideoEmpty.hidden = true;
      liveConnection.innerHTML = "<i></i>新形象预览 · 正在生成";
      if (liveVoiceInput.files[0]) {
        toast("照片和声音已选择，正在重新生成");
        scheduleHostAutoApply("照片和声音已就绪，即将生成…", 1200);
      } else {
        toast("已选择新形象，请继续选择声音或点击生成按钮");
        liveStudioStatus.textContent = "新形象已就绪，等待声音…";
      }
    };
    liveVoiceInput.onchange = function () {
      var file = liveVoiceInput.files[0];
      document.getElementById("liveVoiceName").textContent = file ? file.name + " · 正在应用" : "建议 5–15 秒清晰录音";
      if (!file) return;
      if (livePortraitInput.files[0]) {
        toast("照片和声音已选择，正在重新生成");
        scheduleHostAutoApply("照片和声音已就绪，即将生成…", 1200);
      } else {
        toast("已选择新声音，请继续选择照片或点击生成按钮");
        liveStudioStatus.textContent = "新声音已就绪，等待形象…";
      }
    };
    liveProductVideoInput.onchange = function () {
      document.getElementById("liveProductVideoName").textContent = liveProductVideoInput.files[0] ? liveProductVideoInput.files[0].name : "用于队列首件商品特写";
    };
    liveApplyHostBtn.onclick = createHostSession;
    liveStartBtn.onclick = function () { controlLive("start"); };
    livePauseBtn.onclick = function () { controlLive("pause"); };
    liveResumeBtn.onclick = function () { controlLive("resume"); };
    liveStopBtn.onclick = function () { controlLive("stop"); };

    /* 模拟弹幕与在线人数波动 */
    liveTimers.push(setInterval(function () {
      pushDanmaku(LIVE_DANMAKU[Math.floor(Math.random() * LIVE_DANMAKU.length)]);
    }, 2400));
    liveTimers.push(setInterval(function () {
      state.viewers += Math.floor(Math.random() * 41) - 15;
      if (state.viewers < 8000) state.viewers = 8000;
      renderStats();
    }, 3000));
    pollDigitalHuman();
    liveTimers.push(setInterval(pollDigitalHuman, 1500));

    document.getElementById("liveLikeBtn").onclick = function () {
      state.likes += 1 + Math.floor(Math.random() * 5);
      renderStats();
    };
    document.getElementById("liveFollowBtn").onclick = function () {
      state.followed = !state.followed;
      renderStats();
      toast(state.followed ? "已关注数字人主播" : "已取消关注");
    };
    document.getElementById("liveShareBtn").onclick = function () {
      toast("直播间链接已复制，快分享给好友吧");
    };
    document.getElementById("liveDanmakuForm").onsubmit = function (event) {
      event.preventDefault();
      var input = document.getElementById("liveDanmakuInput");
      var text = input.value.trim();
      if (!text) return;
      pushDanmaku("我：" + text);
      input.value = "";
      if (!state.sessionId || !state.digitalHuman || state.digitalHuman.render_status !== "ready") {
        toast("数字人直播尚未准备完成");
        return;
      }
      var prefix = String(state.digitalHuman.version || "").indexOf("V1") === 0 ? "/api/v1" : "/api/v0";
      api(prefix + "/live/" + state.sessionId + "/danmaku", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, user: maskedAccount(currentAccount) || "商城用户" })
      }).then(function () {
        toast("弹幕已发送，数字人正在生成回答");
        pollDigitalHuman();
      }).catch(function (error) { toast(error.message); });
    };

    renderGoods();
    renderNow();
    renderStats();
    updateStudioControls(null);
    pushDanmaku("欢迎来到 eget 数字人直播间");
    pushDanmaku("今天全场直播价，不要错过");
  }

  function route() {
    if (!currentAccount) { showAuthView("login"); return; }
    if (ordersPollTimer) {
      clearTimeout(ordersPollTimer);
      ordersPollTimer = null;
    }
    clearLiveTimers();
    var hash = location.hash || "#/";
    var parts = hash.slice(2).split("/");
    var navKey = "";
    if (parts[0] === "" ) { navKey = "home"; viewHome(); }
    else if (parts[0] === "live") { navKey = "live"; viewLive(); }
    else if (parts[0] === "cat") { navKey = normalizeCategoryId(decodeURIComponent(parts[1] || "")); viewList(navKey, ""); }
    else if (parts[0] === "search") { viewList("", decodeURIComponent(parts[1] || "")); }
    else if (parts[0] === "product") { viewDetail(parts[1]); }
    else if (parts[0] === "cart") { viewCart(); }
    else if (parts[0] === "checkout") { viewCheckout(); }
    else if (parts[0] === "orders") { viewOrders(); }
    else { viewHome(); }
    document.querySelectorAll("#nav a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-nav") === navKey);
    });
    window.scrollTo(0, 0);
  }

  /* ---------- 首页 ---------- */
  function viewHome() {
    var phones = PRODUCTS.filter(function (p) { return p.cat === "智能手机"; });
    var accs = PRODUCTS.filter(function (p) { return p.cat === "配件产品"; });
    var hero = phones[0] || PRODUCTS[0];
    app.innerHTML =
      '<section class="hero">' +
        '<div>' +
          '<div class="hero-kicker">EGET FLAGSHIP</div>' +
          "<h1>" + esc(hero ? hero.name.split(" ").slice(0, 2).join(" ") : "旗舰新品") + "<br>现已开售</h1>" +
          '<p class="hero-slogan">好物不言，静待知己。</p>' +
          '<div class="hero-actions">' +
            (hero ? '<a class="btn primary" href="#/product/' + hero.sku + '">立即购买</a>' : "") +
            '<a class="btn ghost" href="#/cat/mobile">浏览全部手机</a>' +
          "</div>" +
        "</div>" +
        (hero ? '<img class="hero-img" src="' + hero.img + '" alt="">' : "") +
      "</section>" +
      '<div class="section-head"><h2>热销手机</h2><a href="#/cat/mobile">查看全部 ›</a></div>' +
      '<div class="grid">' + phones.slice(0, 8).map(cardHtml).join("") + "</div>" +
      '<div class="section-head"><h2>精选配件</h2><a href="#/cat/phone-accessories">查看全部 ›</a></div>' +
      '<div class="grid">' + accs.slice(0, 8).map(cardHtml).join("") + "</div>";
  }

  function cardHtml(p) {
    return (
      '<a class="card" href="#/product/' + p.sku + '">' +
        '<img loading="lazy" src="' + p.img + '" alt="">' +
        '<div class="name">' + esc(p.name) + "</div>" +
        '<div class="brief">' + esc(p.brief.split("|")[0] || "") + "</div>" +
        '<div class="price">' + fmtPrice(p.price) +
          (p.market > p.price ? "<s>" + fmtPrice(p.market) + "</s>" : "") +
        "</div>" +
      "</a>"
    );
  }

  /* ---------- 列表页 ---------- */
  function viewList(cat, keyword) {
    cat = normalizeCategoryId(cat || "");
    var items = PRODUCTS.slice();
    if (cat) items = items.filter(function (p) { return productMatchesCategory(p, cat); });
    if (keyword) {
      var k = keyword.toLowerCase();
      items = items.filter(function (p) {
        return (p.name + " " + p.brief).toLowerCase().indexOf(k) >= 0;
      });
    }
    listState = { items: items, shown: 0 };
    var title = keyword ? '搜索“' + esc(keyword) + '”' : esc(cat ? categoryLabel(cat) : "全部商品");
    var categoryCounts = {};
    SHOP_CATEGORIES.forEach(function (category) {
      categoryCounts[category.id || "all"] = PRODUCTS.filter(function (p) { return productMatchesCategory(p, category.id); }).length;
    });
    var chips = SHOP_CATEGORIES.filter(function (category) {
      return !category.id || categoryCounts[category.id] > 0;
    }).map(function (category) {
      var count = categoryCounts[category.id || "all"];
      var active = category.id === cat && !keyword ? " active" : "";
      return '<button class="chip' + active + '" type="button" role="tab" aria-selected="' + (active ? "true" : "false") + '" data-cat="' + category.id + '">' +
        '<span>' + esc(category.label) + '</span><span class="chip-count">' + count + "</span></button>";
    }).join("");
    app.innerHTML =
      '<div class="cat-head"><h1>' + title + "</h1><p>共 " + items.length + " 件商品</p></div>" +
      '<section class="category-panel" aria-label="商品分类"><div class="filter-label">商品分类</div>' +
        '<div class="filter-row" role="tablist">' + chips + "</div></section>" +
      '<div class="grid" id="listGrid"></div>' +
      '<div class="grid-more" id="listMore"></div>';
    app.querySelectorAll(".chip").forEach(function (b) {
      b.onclick = function () {
        var c = b.getAttribute("data-cat");
        location.hash = c ? "#/cat/" + encodeURIComponent(c) : "#/cat/";
      };
    });
    renderMore();
  }

  function renderMore() {
    var grid = document.getElementById("listGrid");
    var more = document.getElementById("listMore");
    if (!grid) return;
    var next = listState.items.slice(listState.shown, listState.shown + PAGE_SIZE);
    grid.insertAdjacentHTML("beforeend", next.map(cardHtml).join(""));
    listState.shown += next.length;
    if (listState.shown === 0) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><div><strong>没有找到相关商品</strong><p>换个关键词试试</p></div></div>';
    }
    if (listState.shown < listState.items.length) {
      more.innerHTML = '<button class="btn ghost" id="moreBtn">加载更多（' + (listState.items.length - listState.shown) + "）</button>";
      document.getElementById("moreBtn").onclick = renderMore;
    } else {
      more.innerHTML = "";
    }
  }

  /* ---------- 详情页 ---------- */
  function viewDetail(sku) {
    var p = PRODUCTS.find(function (x) { return x.sku === sku; });
    if (!p) { app.innerHTML = '<div class="empty"><div><strong>商品不存在</strong></div></div>'; return; }
    var tags = p.brief ? p.brief.split("|").filter(Boolean) : [];
    var detailCategory = productCategoryId(p);
    app.innerHTML =
      '<div class="detail">' +
        '<div class="detail-img"><img src="' + p.img + '" alt=""></div>' +
        '<div class="detail-info">' +
          '<div class="crumb"><a href="#/">首页</a> / <a href="#/cat/' + encodeURIComponent(detailCategory) + '">' + esc(categoryLabel(detailCategory)) + "</a> / 商品详情</div>" +
          "<h1>" + esc(p.name) + "</h1>" +
          '<div class="brief-tags">' + tags.map(function (t) { return "<span>" + esc(t) + "</span>"; }).join("") + "</div>" +
          '<div class="price-box"><span class="now">' + fmtPrice(p.price) + "</span>" +
            (p.market > p.price ? "<s>" + fmtPrice(p.market) + "</s>" : "") +
            '<span class="stock">现货速发</span></div>' +
          '<div class="qty-row"><label>数量</label>' +
            '<div class="qty-ctrl"><button id="qtyMinus">−</button><span id="qtyVal">1</span><button id="qtyPlus">+</button></div>' +
          "</div>" +
          '<div class="detail-actions">' +
            '<button class="btn ghost" id="addCartBtn">加入购物车</button>' +
            '<button class="btn primary" id="buyNowBtn">立即购买</button>' +
          "</div>" +
          '<div class="service-note"><span>官方正品</span><span>7 天无理由退货</span><span>全国联保</span><span>满 99 包邮</span></div>' +
        "</div>" +
      "</div>";
    var qty = 1;
    var qtyVal = document.getElementById("qtyVal");
    document.getElementById("qtyMinus").onclick = function () { if (qty > 1) qty--; qtyVal.textContent = qty; };
    document.getElementById("qtyPlus").onclick = function () { qty++; qtyVal.textContent = qty; };
    document.getElementById("addCartBtn").onclick = function (e) {
      addToCart(p.sku, qty);
      flyToCart(e.target);
    };
    document.getElementById("buyNowBtn").onclick = function () {
      addToCart(p.sku, qty, true);
      location.hash = "#/checkout";
    };
  }

  /* ---------- 购物车 ---------- */
  function viewCart() {
    var cart = getCart();
    if (!cart.length) {
      app.innerHTML = '<div class="empty"><div><strong>购物车还是空的</strong><p>去挑选心仪的商品吧</p><p style="margin-top:18px"><a class="btn primary" href="#/">去逛逛</a></p></div></div>';
      return;
    }
    var total = cart.reduce(function (s, i) { return s + i.price * i.qty; }, 0);
    app.innerHTML =
      '<div class="cat-head"><h1>购物车</h1><p>共 ' + cart.length + " 种商品</p></div>" +
      '<div class="cart-list">' +
        cart.map(function (i, idx) {
          return (
            '<div class="cart-item">' +
              '<a href="#/product/' + i.sku + '"><img src="' + i.img + '" alt=""></a>' +
              '<div class="info"><div class="name">' + esc(i.name) + '</div><div class="brief">' + esc((i.brief || "").split("|")[0]) + "</div></div>" +
              '<div class="qty-ctrl"><button data-op="minus" data-idx="' + idx + '">−</button><span>' + i.qty + '</span><button data-op="plus" data-idx="' + idx + '">+</button></div>' +
              '<div class="price">' + fmtPrice(i.price * i.qty) + "</div>" +
              '<button class="del" data-idx="' + idx + '">删除</button>' +
            "</div>"
          );
        }).join("") +
      "</div>" +
      '<div class="cart-bar"><span class="total">合计：<strong>' + fmtPrice(total) + "</strong></span>" +
        '<a class="btn primary" href="#/checkout">去结算</a></div>';
    app.querySelectorAll(".qty-ctrl button").forEach(function (b) {
      b.onclick = function () {
        var c = getCart();
        var i = +b.getAttribute("data-idx");
        if (b.getAttribute("data-op") === "plus") c[i].qty++;
        else if (c[i].qty > 1) c[i].qty--;
        saveCart(c); viewCart();
      };
    });
    app.querySelectorAll(".del").forEach(function (b) {
      b.onclick = function () {
        var c = getCart();
        c.splice(+b.getAttribute("data-idx"), 1);
        saveCart(c); viewCart();
      };
    });
  }

  /* ---------- 结算 ---------- */
  function viewCheckout() {
    var cart = getCart();
    if (!cart.length) { location.hash = "#/cart"; return; }
    var total = cart.reduce(function (s, i) { return s + i.price * i.qty; }, 0);
    app.innerHTML =
      '<div class="cat-head"><h1>确认订单</h1><p>请核对收货信息与商品清单</p></div>' +
      '<div class="checkout">' +
        '<div class="panel"><h3>收货信息</h3>' +
          '<div class="form-row"><label>收货人</label><input id="buyerName" value="演示用户"></div>' +
          '<div class="form-row"><label>手机号</label><input id="buyerPhone" value="' + esc(currentAccount) + '"></div>' +
          '<div class="form-row"><label>收货地址</label><textarea id="buyerAddr">广东省深圳市南山区 eget 演示大厦 1 层</textarea></div>' +
        "</div>" +
        '<div class="panel"><h3>商品清单</h3>' +
          cart.map(function (i) {
            return '<div class="summary-item"><img src="' + i.img + '"><span class="n">' + esc(i.name) + "</span><span>×" + i.qty + "</span><span>" + fmtPrice(i.price * i.qty) + "</span></div>";
          }).join("") +
          '<div class="summary-row"><span>应付总额</span><strong>' + fmtPrice(total) + "</strong></div>" +
          '<button class="btn primary block" id="submitOrder">提交订单</button>' +
        "</div>" +
      "</div>";
    document.getElementById("submitOrder").onclick = function () {
      var btn = this;
      btn.disabled = true; btn.textContent = "正在下单…";
      api("/api/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          items: cart.map(function (i) { return { sku: i.sku, name: i.name, brief: i.brief, price: i.price, img: i.img, qty: i.qty }; }),
          buyer: {
            name: document.getElementById("buyerName").value,
            phone: document.getElementById("buyerPhone").value,
            address: document.getElementById("buyerAddr").value,
          },
        }),
      }).then(function (res) {
        saveCart([]);
        toast("下单成功，订单号 " + res.order.order_id);
        location.hash = "#/orders";
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = "提交订单";
        toast(e.message);
      });
    };
  }

  /* ---------- 订单页 ---------- */
  function viewOrders() {
    if (!document.getElementById("orderList")) {
      app.innerHTML = '<div class="cat-head"><h1>我的订单</h1><p>售后申请将直接进入智诉决策系统审核，状态每 5 秒自动同步</p></div><div id="orderList"><div class="skeleton"></div></div>';
    }
    api("/api/orders").then(function (res) {
      var box = document.getElementById("orderList");
      if (!box) return;
      if (!res.orders.length) {
        box.innerHTML = '<div class="empty"><div><strong>还没有订单</strong><p>下单后可以在这里申请退款、退货、换货</p><p style="margin-top:18px"><a class="btn primary" href="#/">去购物</a></p></div></div>';
        return;
      }
      box.innerHTML = res.orders.map(orderHtml).join("");
      bindOrderOps(box, res.orders);
      var hasPending = res.orders.some(function (o) {
        var s = (o.aftersale || {}).backend_status;
        return s === "RUNNING" || s === "SUSPENDED";
      });
      if (hasPending) {
        ordersPollTimer = setTimeout(function () {
          if ((location.hash || "#/") === "#/orders") viewOrders();
        }, 5000);
      }
    }).catch(function (e) {
      document.getElementById("orderList").innerHTML = '<div class="empty"><div><strong>' + esc(e.message) + "</strong></div></div>";
    });
  }

  function orderHtml(o) {
    var status = o.status || "";
    var pill = /处理中|审核中|等待/.test(status) ? "busy" :
      (/拒绝|失败|异常/.test(status) ? "danger" :
        (/成功|通过|完成/.test(status) ? "done" : "ok"));
    var items = o.items.map(function (i) {
      return (
        '<div class="order-item"><img src="' + i.img + '">' +
          '<div class="info"><div class="name">' + esc(i.name) + '</div><div class="brief">' + esc((i.brief || "").split("|")[0]) + " ×" + i.qty + "</div></div>" +
          '<span class="amt">' + fmtPrice(i.price * i.qty) + "</span></div>"
      );
    }).join("");
    var opButtons = "";
    if (!o.aftersale) {
      opButtons =
          '<button class="op-btn refund" data-type="refund" data-id="' + o.order_id + '">申请退款</button>' +
          '<button class="op-btn return" data-type="return" data-id="' + o.order_id + '">申请退货</button>' +
          '<button class="op-btn exchange" data-type="exchange" data-id="' + o.order_id + '">申请换货</button>';
    }
    if (o.voucher && o.voucher.url) {
      opButtons += '<button class="op-btn voucher" data-voucher="' + esc(o.voucher.url) + '" data-order="' + esc(o.order_id) + '">订单凭证</button>';
    }
    opButtons += '<button class="op-btn delete" data-delete-order="' + o.order_id + '">删除订单</button>';
    var ops = '<div class="ops">' + opButtons + "</div>";
    var note = "";
    if (o.aftersale) {
      var noteClass = /成功|通过/.test(o.aftersale.status || "") ? " success" :
        (/拒绝|失败|异常/.test(o.aftersale.status || "") ? " danger" : "");
      note =
        '<div class="aftersale-note' + noteClass + '">' +
          "<span>●</span><span>" + esc(o.aftersale.type_label) + "申请已提交智诉决策系统 · 状态 " + esc(o.aftersale.status) + "</span>" +
          '<span class="case">' + esc(o.aftersale.case_id || "") + "</span>" +
          '<a href="javascript:void 0" data-progress="' + esc(o.aftersale.case_id || "") + '">查看进度</a>' +
        "</div>";
    }
    return (
      '<div class="order-card">' +
        '<div class="order-head"><span>' + esc(o.created_at) + "</span><span>订单号 " + esc(o.order_id) + "</span>" +
          '<span class="status-pill ' + pill + '">' + esc(o.status) + "</span></div>" +
        '<div class="order-body">' + items + "</div>" +
        '<div class="order-foot"><span class="paid">实付<strong>' + fmtPrice(o.total) + "</strong></span>" + ops + "</div>" +
        note +
      "</div>"
    );
  }

  function bindOrderOps(box, orders) {
    box.querySelectorAll(".op-btn[data-type]").forEach(function (b) {
      b.onclick = function () {
        var order = orders.find(function (o) { return o.order_id === b.getAttribute("data-id"); });
        openAftersaleModal(order, b.getAttribute("data-type"));
      };
    });
    box.querySelectorAll("[data-progress]").forEach(function (a) {
      a.onclick = function () { openProgressModal(a.getAttribute("data-progress")); };
    });
    box.querySelectorAll("[data-voucher]").forEach(function (b) {
      b.onclick = function () {
        openVoucherModal(b.getAttribute("data-voucher"), b.getAttribute("data-order"));
      };
    });
    box.querySelectorAll("[data-delete-order]").forEach(function (b) {
      b.onclick = function () {
        var orderId = b.getAttribute("data-delete-order");
        var order = orders.find(function (o) { return o.order_id === orderId; });
        var warning = order && order.aftersale
          ? "确定删除这条商城订单吗？\n\n主系统中的退款案件和审计记录会继续保留。"
          : "确定删除这条订单吗？此操作无法撤销。";
        if (!window.confirm(warning)) return;
        b.disabled = true;
        b.textContent = "删除中…";
        api("/api/orders/" + encodeURIComponent(orderId), { method: "DELETE" })
          .then(function (res) {
            toast(res.case_retained ? "订单已删除，退款审计记录已保留" : "订单已删除");
            viewOrders();
          })
          .catch(function (e) {
            b.disabled = false;
            b.textContent = "删除订单";
            toast(e.message);
          });
      };
    });
  }

  function openVoucherModal(url, orderId) {
    apiBlob(url).then(function (blobUrl) {
      modalRoot.innerHTML =
        '<div class="modal-mask" id="voucherMask"><div class="modal voucher-modal">' +
          '<div class="voucher-modal-head"><div><h3>电子订单凭证</h3><p>订单号 ' + esc(orderId) + ' · 售后提交时自动交给 OCR 核验</p></div><button id="voucherClose">×</button></div>' +
          '<img class="voucher-preview" src="' + blobUrl + '" alt="订单凭证">' +
          '<div class="voucher-tip">凭证包含订单号、商品 SKU、商品名称和订单金额，用于订单真实性、商品一致性与价格一致性复核。</div>' +
        '</div></div>';
      function close() {
        URL.revokeObjectURL(blobUrl);
        modalRoot.innerHTML = "";
      }
      document.getElementById("voucherClose").onclick = close;
      document.getElementById("voucherMask").onclick = function (event) {
        if (event.target.id === "voucherMask") close();
      };
    }).catch(function (error) { toast(error.message); });
  }

  /* ---------- 售后申请弹窗 ---------- */
  var TYPE_LABEL = { refund: "仅退款", return: "退货退款", exchange: "换货" };
  var TYPE_HINT = {
    refund: "无需退回商品，仅申请退还货款",
    return: "退回商品后退还货款",
    exchange: "退回商品，更换同款新商品",
  };
  function attachmentPayload(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        resolve({ name: file.name, type: file.type, data: reader.result });
      };
      reader.onerror = function () { reject(new Error("附件读取失败：" + file.name)); };
      reader.readAsDataURL(file);
    });
  }
  function openAftersaleModal(order, type) {
    var first = order.items[0];
    var selectedAttachmentUrls = [];
    modalRoot.innerHTML =
      '<div class="modal-mask" id="mask"><div class="modal aftersale-modal">' +
        "<h3>申请售后</h3>" +
        '<p class="sub">提交后将由智诉决策系统（多Agent）自动核验与审批</p>' +
        '<div class="type-tabs" id="typeTabs">' +
          ["refund", "return", "exchange"].map(function (t) {
            return '<button data-t="' + t + '"' + (t === type ? ' class="active"' : "") + ">" + TYPE_LABEL[t] + "</button>";
          }).join("") +
        "</div>" +
        '<div class="order-mini"><img src="' + first.img + '"><span>' + esc(first.name) + (order.items.length > 1 ? " 等 " + order.items.length + " 件" : "") + '</span><span class="p">' + fmtPrice(order.total) + "</span></div>" +
        '<p class="sub" id="typeHint">' + TYPE_HINT[type] + "</p>" +
        '<div class="form-row evidence-upload-card">' +
          '<div class="evidence-upload-title"><span class="evidence-upload-icon">图</span><div><strong>上传商品附件</strong><small>建议至少上传 1 张，用于核对商品与申请理由</small></div><b>最多 5 张</b></div>' +
          '<label class="attachment-picker" for="asAttachments"><strong>＋ 点击选择商品或破损照片</strong><span>支持 JPG、PNG、WebP，单张不超过 5MB</span></label>' +
          '<input id="asAttachments" class="attachment-input" type="file" accept="image/jpeg,image/png,image/webp" multiple>' +
          '<div id="asFileList" class="attachment-file-list"><div class="attachment-empty">尚未选择附件 · 商城会自动附带订单凭证</div></div>' +
        '</div>' +
        '<div class="form-row"><label>申请理由</label><textarea id="asReason" placeholder="请描述商品问题与诉求，例如：收到商品后发现屏幕有划痕"></textarea></div>' +
        '<div class="modal-actions">' +
          '<button class="btn ghost" id="asCancel">取消</button>' +
          '<button class="btn primary" id="asSubmit">提交申请</button>' +
        "</div>" +
      "</div></div>";
    var curType = type;
    document.getElementById("typeTabs").querySelectorAll("button").forEach(function (b) {
      b.onclick = function () {
        curType = b.getAttribute("data-t");
        this.parentNode.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        document.getElementById("typeHint").textContent = TYPE_HINT[curType];
      };
    });
    var attachmentInput = document.getElementById("asAttachments");
    attachmentInput.onchange = function () {
      var files = Array.from(this.files || []);
      var list = document.getElementById("asFileList");
      selectedAttachmentUrls.forEach(function (url) { URL.revokeObjectURL(url); });
      selectedAttachmentUrls = [];
      if (!files.length) {
        list.innerHTML = '<div class="attachment-empty">尚未选择附件 · 商城会自动附带订单凭证</div>';
        return;
      }
      list.innerHTML = files.map(function (file) {
        var previewUrl = URL.createObjectURL(file);
        selectedAttachmentUrls.push(previewUrl);
        return '<div class="attachment-file-item"><img src="' + previewUrl + '" alt="附件预览"><div><strong>' +
          esc(file.name) + '</strong><span>' + (file.size / 1024 / 1024).toFixed(2) +
          ' MB</span></div><em>已选择</em></div>';
      }).join("");
    };
    function close() {
      selectedAttachmentUrls.forEach(function (url) { URL.revokeObjectURL(url); });
      selectedAttachmentUrls = [];
      modalRoot.innerHTML = "";
    }
    document.getElementById("asCancel").onclick = close;
    document.getElementById("mask").onclick = function (e) { if (e.target.id === "mask") close(); };
    document.getElementById("asSubmit").onclick = function () {
      var reason = document.getElementById("asReason").value.trim();
      if (!reason) { toast("请填写申请理由"); return; }
      var files = Array.from(attachmentInput.files || []);
      if (files.length > 5) { toast("实物附件最多上传5张"); return; }
      if (files.some(function (file) { return !/image\/(jpeg|png|webp)/.test(file.type) || file.size > 5 * 1024 * 1024; })) {
        toast("附件仅支持5MB以内的 JPG/PNG/WebP 图片"); return;
      }
      var btn = this;
      btn.disabled = true; btn.textContent = "正在提交智诉决策系统…";
      Promise.all(files.map(attachmentPayload)).then(function (attachments) {
        return api("/api/orders/" + order.order_id + "/aftersale", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: curType, reason: reason, attachments: attachments }),
        });
      }).then(function (res) {
        showResult(true, res, order, curType);
      }).catch(function (e) {
        showResult(false, { message: e.message });
      });
    };
  }

  function showResult(ok, res, order, type) {
    if (ok) {
      modalRoot.innerHTML =
        '<div class="modal-mask"><div class="modal"><div class="result-box">' +
          '<div class="icon">✓</div>' +
          "<h4>" + TYPE_LABEL[type] + "申请已受理</h4>" +
          "<p>智诉决策系统已受理该案件，多Agent 正在执行<br>订单核验 · 凭证 OCR · 舆情风控 · 决策审批</p>" +
          '<span class="case-id">案件号 ' + esc(res.case_id) + "</span>" +
          '<div class="modal-actions"><button class="btn primary block" onclick="location.hash=\'#/orders\';location.reload()">返回订单查看进度</button></div>' +
        "</div></div></div>";
    } else {
      modalRoot.innerHTML =
        '<div class="modal-mask"><div class="modal"><div class="result-box">' +
          '<div class="icon err">!</div>' +
          "<h4>提交失败</h4><p>" + esc(res.message) + "</p>" +
          '<div class="modal-actions"><button class="btn ghost block" id="resultClose">关闭</button></div>' +
        "</div></div></div>";
      document.getElementById("resultClose").onclick = function () { modalRoot.innerHTML = ""; };
    }
  }

  /* ---------- 进度查询 ---------- */
  var AGENT_LABELS = {
    security_gate: "安全合规校验", intake: "售后申请受理", verification: "订单、商品与价格核验",
    ocr: "凭证智能识别", fraud: "异常与欺诈风险识别", sentiment: "舆情影响分析",
    amount_decision: "综合退款决策", human_review: "主管人工审批",
    refund_gate: "退款安全校验", refund_execution: "退款执行", finalize: "结果归档",
  };
  var AGENT_STATUS_LABELS = {
    SUCCESS: "已完成", RUNNING: "处理中", PENDING: "等待处理", FAILED: "处理异常",
    SKIPPED: "无需执行", BLOCKED: "已拦截", TIMEOUT: "处理超时",
  };
  var VERDICT_LABELS = { MATCH: "核验一致", MISMATCH: "信息不一致", UNKNOWN: "等待人工确认" };

  function progressState(c) {
    if (c.current_status === "COMPLETED" && c.final_decision === "REJECTED") {
      return { tone: "danger", icon: "×", title: "售后申请未通过", desc: "主管已完成审核，本次申请未通过。" };
    }
    if (c.current_status === "COMPLETED" && c.final_decision === "SYSTEM_FAILURE") {
      return { tone: "danger", icon: "!", title: "售后处理出现异常", desc: "系统已记录异常，请联系人工客服处理。" };
    }
    if (c.current_status === "COMPLETED" && c.final_decision === "APPROVED" && c.refund_status === "FAILED") {
      return { tone: "danger", icon: "!", title: "退款执行失败", desc: "申请已通过，但退款暂未成功，工作人员会继续处理。" };
    }
    if (c.current_status === "COMPLETED" && c.final_decision === "APPROVED") {
      return { tone: "success", icon: "✓", title: c.refund_status === "SUCCESS" ? "退款已成功" : "售后申请已通过", desc: c.refund_status === "SUCCESS" ? "款项已按原支付路径退回，请留意到账通知。" : "主管已审批通过，后续处理正在按流程进行。" };
    }
    if (c.current_status === "SUSPENDED") {
      return { tone: "waiting", icon: "…", title: "等待主管审批", desc: "智能审核已经完成，申请正在等待主管人工确认。" };
    }
    return { tone: "running", icon: "↻", title: "智能审核进行中", desc: "多个智能 Agent 正在核验订单、凭证、风险与舆情信息。" };
  }

  function progressSteps(c) {
    var completed = c.current_status === "COMPLETED";
    var suspended = c.current_status === "SUSPENDED";
    var hasHuman = (c.agents || []).some(function (a) { return a.agent_name === "human_review"; });
    return [
      { title: "申请已受理", desc: "售后申请已安全进入退款决策系统", state: "done" },
      { title: "多 Agent 智能审核", desc: "核验订单、商品、价格、凭证、风险和舆情", state: suspended || completed ? "done" : "active" },
      { title: "主管人工审批", desc: hasHuman ? "主管已提交审批结论" : (suspended ? "正在等待主管确认处理意见" : "系统将依据风险决定是否需要人工复核"), state: hasHuman ? "done" : (suspended ? "active" : (completed ? "skip" : "pending")) },
      { title: "完成售后处理", desc: completed ? (c.final_decision === "APPROVED" ? "审批结论已生效" : "本次售后流程已结束") : "审批完成后将更新最终结果", state: completed ? (c.final_decision === "SYSTEM_FAILURE" || c.refund_status === "FAILED" ? "error" : "done") : "pending" },
    ];
  }

  function verificationHtml(c) {
    var rows = [
      ["订单真实性", c.order_verification], ["购买商品一致性", c.product_verification], ["订单价格一致性", c.price_verification],
    ];
    if (!rows.some(function (row) { return row[1]; })) return "";
    return '<section class="progress-section"><h4>关键信息核验</h4><div class="verify-grid">' + rows.map(function (row) {
      var tone = row[1] === "MATCH" ? "pass" : row[1] === "MISMATCH" ? "fail" : "wait";
      return '<div class="verify-card ' + tone + '"><span>' + esc(row[0]) + '</span><strong>' + esc(VERDICT_LABELS[row[1]] || "核验中") + '</strong></div>';
    }).join("") + '</div></section>';
  }

  function agentListHtml(agents) {
    if (!agents || !agents.length) return '<div class="progress-empty">执行记录正在生成，请稍后刷新查看</div>';
    return '<section class="progress-section"><h4>智能处理记录</h4><div class="agent-records">' + agents.map(function (agent) {
      var tone = agent.status === "SUCCESS" ? "success" : agent.status === "FAILED" ? "danger" : "running";
      var duration = agent.duration_ms != null ? '<small>' + esc(agent.duration_ms) + ' 毫秒</small>' : "";
      return '<div class="agent-record"><span class="agent-dot ' + tone + '"></span><div><strong>' + esc(AGENT_LABELS[agent.agent_name] || "智能流程处理") + '</strong><span>' + esc(AGENT_STATUS_LABELS[agent.status] || "处理中") + '</span></div>' + duration + '</div>';
    }).join("") + '</div></section>';
  }

  function openProgressModal(caseId) {
    modalRoot.innerHTML =
      '<div class="modal-mask" id="mask"><div class="modal progress-modal">' +
        '<div class="progress-modal-head"><div><h3>售后处理进度</h3><p class="sub">案件编号：' + esc(caseId) + '</p></div><span class="live-badge">实时同步</span></div>' +
        '<div id="progBody"><div class="progress-loading"><div class="spinner"></div><p>正在同步最新处理进度…</p></div></div>' +
        '<div class="modal-actions"><button class="btn ghost block" id="progClose">关闭</button></div>' +
      "</div></div>";
    document.getElementById("progClose").onclick = function () { modalRoot.innerHTML = ""; };
    document.getElementById("mask").onclick = function (e) { if (e.target.id === "mask") modalRoot.innerHTML = ""; };
    api("/api/case/" + caseId).then(function (c) {
      var state = progressState(c);
      var steps = progressSteps(c);
      var decisionText = { PENDING: "等待决策", APPROVED: "审批通过", REJECTED: "审批未通过", SYSTEM_FAILURE: "系统异常" }[c.final_decision] || "处理中";
      var refundText = { NONE: "尚未发起", PENDING: "退款处理中", PROCESSING: "退款处理中", SUCCESS: "退款成功", FAILED: "退款失败" }[c.refund_status] || "尚未发起";
      var riskText = c.fraud_score == null ? "分析中" : (c.fraud_score >= 60 ? "较高风险" : c.fraud_score > 20 ? "一般风险" : "低风险");
      var body = document.getElementById("progBody");
      if (!body) return;
      body.innerHTML =
        '<div class="progress-summary ' + state.tone + '"><span class="progress-icon">' + state.icon + '</span><div><h4>' + state.title + '</h4><p>' + state.desc + '</p></div></div>' +
        '<div class="progress-timeline">' + steps.map(function (step, index) {
          return '<div class="progress-step ' + step.state + '"><div class="step-marker">' + (step.state === "done" ? "✓" : index + 1) + '</div><div><strong>' + step.title + '</strong><p>' + step.desc + '</p></div></div>';
        }).join("") + '</div>' +
        '<div class="progress-facts"><div><span>审批结论</span><strong>' + decisionText + '</strong></div><div><span>退款状态</span><strong>' + refundText + '</strong></div><div><span>风险判断</span><strong>' + riskText + '</strong></div></div>' +
        verificationHtml(c) + agentListHtml(c.agents);
    }).catch(function (e) {
      var body = document.getElementById("progBody");
      if (body) body.innerHTML = '<div class="progress-error"><span>!</span><strong>暂时无法获取进度</strong><p>' + esc(e.message) + "</p></div>";
    });
  }

  /* ---------- 启动 ---------- */
  searchInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && searchInput.value.trim()) {
      location.hash = "#/search/" + encodeURIComponent(searchInput.value.trim());
    }
  });
  logoutBtn.onclick = function () { signOut(true); };
  if (location.pathname.indexOf("/digital-human") === 0) {
    authToken = "autodl-standalone";
    enterStore(authToken, "数字人运营");
  } else if (authToken) {
    api("/api/auth/session").then(function (session) {
      enterStore(authToken, session.account);
    }).catch(function () {
      authToken = "";
      localStorage.removeItem("eget_auth_token");
      showAuthView("login");
    });
  } else {
    showAuthView("login");
  }
  window.addEventListener("hashchange", route);
})();
