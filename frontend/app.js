const state = {
  market: null,
  sectors: null,
  candidates: null,
  holdings: null,
  intraday: null,
  review: null,
  watchlist: null,
  today: null,
  stage: "intraday",
};

const viewMeta = {
  today: ["今日", "今日交易驾驶舱"],
  premarket: ["持仓 / 盘前", "早盘计划"],
  intraday: ["持仓 / 盘中", "盘中监控"],
  review: ["持仓 / 盘后", "盘后复盘"],
  sectors: ["选股 / 板块", "强势板块"],
  stocks: ["选股 / 个股", "强势个股"],
  pullback: ["选股 / 观察", "回踩观察"],
  watchlist: ["观察", "观察池"],
};

const pct = (value, digits = 2) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "暂无数据";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
};

const number = (value, digits = 2) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "暂无数据";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);
};

const amount = (value) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "暂无数据";
  return `${(value / 100000000).toFixed(0)} 亿`;
};

const money = (value, signed = false) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "暂无数据";
  return `${value < 0 ? "-" : signed && value > 0 ? "+" : ""}¥${number(Math.abs(value))}`;
};

const changeClass = (value) => value > 0 ? "up" : value < 0 ? "down" : "flat";
const statusClass = (value) => value === "已满足" ? "met" : value === "接近满足" ? "near" : "";
const sourceLabel = () => state.market?.data_source?.label || state.market?.data_source || "--";
const sourceTime = () => state.market?.data_source?.retrieved_at?.slice(11, 16) || "--";
const shown = (value) => value === null || value === undefined || value === "" ? "暂无数据" : value;

const pills = (items = []) => `
  <div class="pills">${items.map((item) => `<span>${item}</span>`).join("")}</div>
`;

const stat = (label, value, note = "") => `
  <div class="stat"><span>${label}</span><strong>${value}</strong>${note ? `<small>${note}</small>` : ""}</div>
`;

const sectionHead = (title, note = "") => `
  <div class="section-head"><h2>${title}</h2>${note ? `<p>${note}</p>` : ""}</div>
`;

const holdingTabs = (active) => `
  <div class="local-tabs">
    <button data-view="premarket" class="${active === "premarket" ? "active" : ""}">早盘计划</button>
    <button data-view="intraday" class="${active === "intraday" ? "active" : ""}">盘中监控</button>
    <button data-view="review" class="${active === "review" ? "active" : ""}">盘后复盘</button>
  </div>
`;

const selectionTabs = (active) => `
  <div class="local-tabs">
    <button data-view="sectors" class="${active === "sectors" ? "active" : ""}">强势板块</button>
    <button data-view="stocks" class="${active === "stocks" ? "active" : ""}">强势个股</button>
    <button data-view="pullback" class="${active === "pullback" ? "active" : ""}">回踩观察</button>
  </div>
`;

const miniMetrics = (items) => `
  <div class="mini-metrics">
    ${items.map(([label, value, cls = ""]) => `<div><span>${label}</span><strong class="${cls}">${value}</strong></div>`).join("")}
  </div>
`;

const conditionList = (conditions) => `
  <div class="condition-list">
    ${conditions.map((condition) => `
      <div class="condition">
        <div><strong>${condition.name}</strong><span class="condition-state ${statusClass(condition.status)}">${condition.status}</span></div>
        <small>${condition.met_count}/${condition.total_count} 项已符合${condition.missing_count ? ` · ${condition.missing_count} 项数据缺失` : ""}</small>
      </div>
    `).join("")}
  </div>
`;

const candidateCard = (stock) => `
  <article class="candidate-row">
    <div class="stock-title">
      <div><strong>${stock.name}</strong><span>${stock.code} · ${shown(stock.industry)}</span></div>
      <div class="stock-price"><strong>${number(stock.close)}</strong><span class="${changeClass(stock.change_pct)}">${pct(stock.change_pct)}</span></div>
    </div>
    ${miniMetrics([
      ["MA20 距离", pct(stock.ma20_distance)],
      ["量能 / 5日", number(stock.volume_ratio_5d)],
      ["板块强度", stock.industry_rank === null || stock.industry_rank === undefined ? stock.sector.status : `${stock.sector.status} · ${stock.industry_rank}/${stock.sector.rank_total}`],
      ["个股相对", pct(stock.relative_sector_strength), changeClass(stock.relative_sector_strength)],
    ])}
    <div class="candidate-status">
      <span>${stock.strength_relation}</span>
      <strong class="${statusClass(stock.selection_status === "条件已触发" ? "已满足" : stock.selection_status === "接近触发" ? "接近满足" : "")}">${stock.selection_status}</strong>
    </div>
    <details>
      <summary>为什么入选与触发条件</summary>
      <div class="detail-grid">
        <div><h3>入选理由</h3><ul>${stock.selection_reasons.map((item) => `<li>${item}</li>`).join("")}</ul></div>
        <div><h3>我在等什么</h3><ul>${stock.waiting_for.map((item) => `<li>${item}</li>`).join("") || "<li>当前规则条件已有触发，继续观察结构是否保持。</li>"}</ul></div>
      </div>
      <h3>买入触发条件</h3>
      ${conditionList(stock.conditions)}
    </details>
  </article>
`;

function renderToday() {
  const market = state.market;
  const summary = state.holdings.summary;
  const today = state.today;
  document.querySelector("#today").innerHTML = `
    <section class="market-band">
      <div class="market-state">
        <span class="stage-label" id="home-stage">${market.stage_options.find((item) => item.id === state.stage)?.label || "盘后"}</span>
        <div><p>今日状态</p><h2>${market.status_labels.join(" · ")}</h2></div>
      </div>
      <div class="index-strip">
        ${market.indices.map((item) => `<div><span>${item.name}</span><strong>${number(item.value)}</strong><small class="${changeClass(item.change_pct)}">${pct(item.change_pct)}</small></div>`).join("")}
      </div>
      <div class="market-facts">
        ${stat("两市成交额", amount(market.turnover), market.turnover_change === null ? "" : pct(market.turnover_change) + " 较前日")}
        ${stat("上涨 / 下跌", `${shown(market.advance_count)} / ${shown(market.decline_count)}`)}
        ${stat("涨停数量", shown(market.limit_up_count))}
        ${stat("核心板块", market.core_sectors.map((item) => item.name).join("、") || "暂无数据")}
      </div>
    </section>

    <section class="content-section priority-section">
      ${sectionHead("我的持仓状态", `${summary.holding_count} 只持仓 · 异常优先排序`)}
      <div class="summary-numbers">
        ${stat("组合今日涨跌", pct(summary.portfolio_change_pct), `浮动盈亏 ${money(summary.total_unrealized_pnl, true)}`)}
        ${stat("MA20 上 / 下", `${summary.above_ma20_count} / ${summary.below_ma20_count}`)}
        ${stat("强于 / 弱于板块", `${summary.stronger_than_sector_count} / ${summary.weaker_than_sector_count}`)}
        ${stat("需要关注", summary.anomaly_count)}
      </div>
      <div class="mobile-candidate-inline"><span>今日候选</span><strong>${today.summary.candidate_count}</strong><small>只进入核心池</small></div>
      <div class="anomaly-list">
        ${today.holding_anomalies.map((item) => `<button data-view="intraday"><strong>${item.name}</strong><span>${item.labels.join(" · ")}</span><b>查看</b></button>`).join("")}
      </div>
      ${summary.holding_count === 0 ? '<p class="empty">尚未配置真实持仓。</p>' : ""}
    </section>

    <section class="content-section attention-grid">
      <div>
        ${sectionHead("今日需要关注")}
        <ul class="focus-list">${today.attention.map((item) => `<li>${item}</li>`).join("")}</ul>
      </div>
      <div class="candidate-count">
        <span>今日候选</span><strong>${today.summary.candidate_count}</strong><small>规则扫描后进入核心池</small>
      </div>
    </section>

    <section class="content-section">
      ${sectionHead("今日候选", "优先展示板块与个股关系清晰的标的")}
      <div class="candidate-list">${today.top_candidates.map(candidateCard).join("") || '<p class="empty">今日没有进入核心池的候选。</p>'}</div>
    </section>
  `;
}

function renderPremarket() {
  document.querySelector("#premarket").innerHTML = `
    ${holdingTabs("premarket")}
    <div class="phase-note"><strong>盘前依据昨日收盘数据生成</strong><span>观察位和条件式计划均由规则输出</span></div>
    <div class="holding-list">
      ${state.holdings.premarket.map((stock) => `
        <article class="holding-row attention-${stock.attention_level === "需要关注" ? "high" : "normal"}">
          <div class="stock-title">
            <div><strong>${stock.name}</strong><span>${stock.code} · ${stock.industry} · ${stock.attention_level}</span></div>
            <div class="stock-price"><strong>${number(stock.close)}</strong><span class="${changeClass(stock.position_return)}">浮盈亏 ${pct(stock.position_return)}</span></div>
          </div>
          ${miniMetrics([
            ["持仓 / 成本总额", `${number(stock.shares, 0)} 股 / ${money(stock.cost_basis)}`],
            ["当前市值 / 浮盈亏", `${money(stock.market_value)} / ${money(stock.unrealized_pnl, true)}`, changeClass(stock.unrealized_pnl)],
            ["昨日收盘 / 成本", `${number(stock.close)} / ${number(stock.cost, 3)}`],
            ["MA5 / MA10 / MA20", `${number(stock.ma5)} / ${number(stock.ma10)} / ${number(stock.ma20)}`],
            ["距 MA20", pct(stock.ma20_distance)],
            ["昨日量能", stock.yesterday_volume_state],
            ["板块昨日表现", pct(stock.industry_return), changeClass(stock.industry_return)],
            ["个股相对板块", pct(stock.relative_sector_strength), changeClass(stock.relative_sector_strength)],
            ["昨日高 / 低", `${number(stock.high)} / ${number(stock.low)}`],
            ["前高 / 近期支撑", `${number(stock.previous_high)} / ${number(stock.recent_support)}`],
            ["所属概念", stock.concepts?.slice(0, 3).map((item) => item.name).join("、") || "暂无数据"],
            ["行情时间", shown(stock.source_timestamp?.replace("T", " "))],
          ])}
          <div class="two-columns">
            <div><h3>今日观察位</h3><div class="levels">${stock.observation_levels.map((item) => `<span><small>${item.label}</small><strong>${number(item.value)}</strong></span>`).join("")}</div></div>
            <div><h3>今日条件式计划</h3><ul>${stock.conditional_plan.map((item) => `<li>${item}</li>`).join("")}</ul></div>
          </div>
        </article>
      `).join("") || '<p class="empty">尚未配置真实持仓。</p>'}
    </div>
  `;
}

function renderIntraday() {
  document.querySelector("#intraday").innerHTML = `
    ${holdingTabs("intraday")}
    <div class="phase-note live"><strong>${sourceLabel()}行情 · ${sourceTime()}</strong><span>${state.market.freshness === "stale" ? "实时行情暂未更新" : "异常状态置顶"}</span></div>
    <div class="holding-list">
      ${state.intraday.holdings.map((stock) => `
        <article class="holding-row">
          <div class="stock-title">
            <div><strong>${stock.name}</strong><span>${stock.code} · ${stock.industry}</span></div>
            <div class="stock-price"><strong>${number(stock.current_price)}</strong><span class="${changeClass(stock.change_pct)}">${pct(stock.change_pct)}</span></div>
          </div>
          ${pills(stock.statuses)}
          ${miniMetrics([
            ["持仓成本 / 股数", `${number(stock.cost, 3)} / ${number(stock.shares, 0)} 股`],
            ["当前市值 / 浮盈亏", `${money(stock.market_value)} / ${money(stock.unrealized_pnl, true)}`, changeClass(stock.unrealized_pnl)],
            ["浮盈亏比例", pct(stock.position_return), changeClass(stock.position_return)],
            ["今日高 / 低", `${number(stock.high)} / ${number(stock.low)}`],
            ["振幅", pct(stock.amplitude)],
            ["当前成交量", number(stock.volume, 0)],
            ["5日均量 / 当日量比", `${number(stock.volume_ma5, 0)} / ${number(stock.volume_ratio_5d)}`],
            ["量能状态", shown(stock.volume_state)],
            ["换手率", pct(stock.turnover_rate)],
            ["实时量比", number(stock.realtime_volume_ratio)],
            ["VWAP", number(stock.vwap)],
            ["MA20 / 距离", `${number(stock.ma20)} / ${pct(stock.ma20_distance)}`],
            ["板块实时涨跌", pct(stock.sector_return), changeClass(stock.sector_return)],
            ["个股相对板块", pct(stock.relative_sector_strength), changeClass(stock.relative_sector_strength)],
            ["行情时间", shown(stock.source_timestamp?.replace("T", " "))],
          ])}
          <div class="rule-note"><span>盘中规则观察</span><p>${stock.conditional_note}</p></div>
        </article>
      `).join("") || '<p class="empty">尚未配置真实持仓。</p>'}
    </div>
  `;
}

function renderReview() {
  const summary = state.review.summary;
  document.querySelector("#review").innerHTML = `
    ${holdingTabs("review")}
    <section class="review-summary">
      ${sectionHead("今日组合总结")}
      <div class="summary-numbers">
        ${stat("组合涨跌", pct(summary.portfolio_change_pct))}
        ${stat("持仓数量", summary.holding_count)}
        ${stat("持仓市值", money(summary.total_market_value))}
        ${stat("持仓总成本", money(summary.total_cost_basis))}
        ${stat("浮动盈亏", money(summary.total_unrealized_pnl, true), pct(summary.total_unrealized_pnl_pct))}
        ${stat("强 / 弱于板块", `${summary.stronger_than_sector_count} / ${summary.weaker_than_sector_count}`)}
        ${stat("MA20 上 / 下", `${summary.above_ma20_count} / ${summary.below_ma20_count}`)}
        ${stat("缩量回踩", summary.shrink_pullback_count)}
        ${stat("放量异常", summary.abnormal_volume_count)}
        ${stat("趋势破坏", summary.trend_break_count)}
      </div>
      <p class="narrative">${summary.narrative}</p>
    </section>
    <section class="content-section">
      ${sectionHead("逐只复盘", "优先记录今天发生的变化")}
      <div class="review-list">
        ${state.review.holdings.map((stock) => `
          <article>
            <div class="stock-title"><div><strong>${stock.name}</strong><span>${stock.code} · ${shown(stock.industry)}</span></div><div class="stock-price"><strong class="${changeClass(stock.change_pct)}">${pct(stock.change_pct)}</strong><span class="${changeClass(stock.unrealized_pnl)}">浮盈亏 ${money(stock.unrealized_pnl, true)}</span></div></div>
            <h3>今日发生了什么</h3><p>${stock.what_happened}</p>
            <h3>相比昨日发生什么变化</h3><ul>${stock.changes_from_yesterday.map((item) => `<li>${item}</li>`).join("")}</ul>
          </article>
        `).join("") || '<p class="empty">尚未配置真实持仓。</p>'}
      </div>
    </section>
  `;
}

function renderSectors() {
  document.querySelector("#sectors").innerHTML = `
    ${selectionTabs("sectors")}
    <div class="sector-list">
      ${state.sectors.sectors.map((sector) => `
        <article class="sector-row">
          <div class="sector-rank"><span>${shown(sector.rank)}</span><small>${sector.rank_total == null ? "" : `/ ${sector.rank_total}`}</small></div>
          <div class="sector-main"><div><strong>${sector.name}</strong><span class="sector-status">${sector.status}</span></div>
            ${miniMetrics([
              ["今日 / 5日 / 20日", `${pct(sector.return_pct)} / ${pct(sector.return_5d)} / ${pct(sector.return_20d)}`],
              ["上涨家数比例", pct(sector.advance_ratio)],
              ["涨停数量", shown(sector.limit_up_count)],
              ["成交额变化", pct(sector.turnover_change)],
              ["5日成交额变化", pct(sector.turnover_5d_change)],
              ["板块趋势", sector.ma20_status],
            ])}
          </div>
        </article>
      `).join("") || '<p class="empty">暂无可核实的板块数据。</p>'}
    </div>
  `;
}

function renderCandidateView(target, items, active, note) {
  document.querySelector(target).innerHTML = `
    ${selectionTabs(active)}
    <div class="phase-note"><strong>${note}</strong><span>所有状态均由配置规则计算</span></div>
    <div class="candidate-list">${items.map(candidateCard).join("") || '<p class="empty">当前没有符合该分组规则的候选。</p>'}</div>
  `;
}

function renderWatchlist() {
  document.querySelector("#watchlist").innerHTML = `
    <div class="phase-note"><strong>观察结果持续记录</strong><span>用于验证不同入池逻辑的实际表现</span></div>
    <div class="watch-list">
      ${state.watchlist.watchlist.map((item) => `
        <article class="watch-row">
          <div class="stock-title">
            <div><strong>${item.name}</strong><span>${item.code} · ${shown(item.industry)} · 加入 ${shown(item.added_date)}</span></div>
            <div class="stock-price"><strong>${number(item.current_price)}</strong><span class="${changeClass(item.return_since_added)}">${pct(item.return_since_added)}</span></div>
          </div>
          ${miniMetrics([
            ["加入价格", number(item.added_price)],
            ["已观察交易日", shown(item.observed_trading_days)],
            ["最大涨幅", pct(item.max_gain), changeClass(item.max_gain)],
            ["最大回撤", pct(item.max_drawdown), changeClass(item.max_drawdown)],
            ["买入条件状态", shown(item.buy_condition_status)],
            ["当前 MA20 距离", pct(item.current_ma20_distance)],
          ])}
          <div class="two-columns">
            <div><h3>当前触发条件</h3><p>${item.current_triggered_conditions.join("、") || "暂无完整触发条件"}</p></div>
            <div><h3>原始入池原因</h3><p>${item.original_reason.join("、") || "暂无数据"}</p></div>
          </div>
        </article>
      `).join("") || '<p class="empty">观察池为空；旧 Mock 记录未作为真实历史发布。</p>'}
    </div>
  `;
}

function bindDynamicNavigation() {
  document.querySelectorAll(".view [data-view]").forEach((button) => {
    if (button.dataset.bound === "true") return;
    button.dataset.bound = "true";
    button.addEventListener("click", () => setView(button.dataset.view));
  });
}

function renderAll() {
  renderToday();
  renderPremarket();
  renderIntraday();
  renderReview();
  renderSectors();
  renderCandidateView("#stocks", state.candidates.strong_stocks, "stocks", "板块与个股强弱关系优先");
  renderCandidateView("#pullback", state.candidates.pullback_watch, "pullback", "接近回踩条件的候选");
  renderWatchlist();
  bindDynamicNavigation();
}

function setView(view) {
  document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === view));
  document.querySelectorAll(".desktop-nav [data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  const [crumb, title] = viewMeta[view];
  document.querySelector("#breadcrumb").textContent = crumb;
  document.querySelector("#view-title").textContent = title;
  const mobileGroup = view === "today" ? "today" : ["premarket", "intraday", "review"].includes(view) ? "holdings" : ["sectors", "stocks", "pullback"].includes(view) ? "selection" : "watchlist";
  document.querySelectorAll("[data-mobile-view]").forEach((button) => button.classList.toggle("active", button.dataset.mobileView === mobileGroup));
  window.scrollTo({ top: 0, behavior: "instant" });
}

function setStage(stage) {
  state.stage = stage;
  document.querySelectorAll("[data-stage]").forEach((button) => button.classList.toggle("active", button.dataset.stage === stage));
  renderToday();
  bindDynamicNavigation();
  if (stage === "premarket") setView("premarket");
  if (stage === "intraday") setView("intraday");
  if (stage === "postmarket") setView("review");
}

async function loadData() {
  const names = ["market", "sectors", "candidates", "holdings", "intraday", "review", "watchlist", "today"];
  const payloads = await Promise.all(names.map((name) => fetch(`../data/${name}.json`).then((response) => {
    if (!response.ok) throw new Error(`${name}.json 加载失败`);
    return response.json();
  })));
  names.forEach((name, index) => { state[name] = payloads[index]; });
  state.stage = state.market.default_stage;
  document.querySelector("#trade-date").textContent = `最近交易日 · ${shown(state.market.trade_date)}`;
  document.querySelector("#source-label").textContent = `数据源 · ${state.market.data_source.label}`;
  const freshness = state.market.data_source.freshness;
  const freshnessText = freshness === "current" ? "当日行情" : freshness === "last_trading_day" ? "上一交易日" : freshness === "stale" ? "实时行情暂未更新" : "时间待核实";
  const fallbackText = state.market.data_source.fallback ? " · 部分行情使用备用数据源" : "";
  const status = document.querySelector("#data-status");
  status.textContent = `${freshnessText} · 更新 ${sourceTime()}${fallbackText}`;
  status.classList.toggle("stale", freshness === "stale");
  document.querySelector("#loading").hidden = true;
  renderAll();
  setView("today");
}

document.querySelectorAll(".desktop-nav [data-view]").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

document.querySelectorAll("[data-stage]").forEach((button) => {
  button.addEventListener("click", () => setStage(button.dataset.stage));
});

document.querySelectorAll("[data-mobile-view]").forEach((button) => {
  button.addEventListener("click", () => {
    const map = { today: "today", holdings: state.stage === "premarket" ? "premarket" : state.stage === "postmarket" ? "review" : "intraday", selection: "sectors", watchlist: "watchlist" };
    setView(map[button.dataset.mobileView]);
  });
});

loadData().catch((error) => {
  document.querySelector("#loading").innerHTML = `<strong>数据加载失败</strong><span>${error.message}</span>`;
});
