const state = {
  today: null,
  holdings: null,
  watchlist: null,
};

const titles = {
  overview: "今日总览",
  candidates: "今日候选",
  holdings: "我的持仓",
  review: "今日复盘",
  watchlist: "观察池",
};

const pct = (value) => {
  if (value === null || value === undefined) return "缺失";
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(2)}%`;
};

const number = (value) => {
  if (value === null || value === undefined) return "缺失";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
};

const changeClass = (value) => {
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "flat";
};

const tagClass = (tag) => {
  const keys = ["观察", "MA20上方", "缩量", "强于板块"];
  return keys.includes(tag) ? "tag key" : "tag";
};

const tags = (items) => `
  <div class="tags">
    ${items.map((tag) => `<span class="${tagClass(tag)}">${tag}</span>`).join("")}
  </div>
`;

const metric = (label, value, note = "") => `
  <article class="metric">
    <span>${label}</span>
    <strong>${value}</strong>
    ${note ? `<small>${note}</small>` : ""}
  </article>
`;

const stockCard = (stock, extra = "") => `
  <article class="stock-card">
    <div class="stock-head">
      <div class="stock-name">
        <strong>${stock.name}</strong>
        <span>${stock.code} · ${stock.industry}</span>
      </div>
      <div class="price">
        <strong>${number(stock.close ?? stock.current_price)}</strong>
        <span class="${changeClass(stock.change_pct)}">${pct(stock.change_pct)}</span>
      </div>
    </div>
    <div class="kv-grid">
      <div class="kv"><span>MA20距离</span><strong>${pct(stock.ma20_distance ?? stock.current_ma20_distance)}</strong></div>
      <div class="kv"><span>量比5日</span><strong>${number(stock.volume_ratio_5d)}</strong></div>
      <div class="kv"><span>相对板块</span><strong class="${changeClass(stock.relative_industry_strength)}">${pct(stock.relative_industry_strength)}</strong></div>
    </div>
    ${extra}
    ${tags(stock.labels || stock.current_labels || [])}
  </article>
`;

const renderOverview = () => {
  const today = state.today.summary;
  const holdings = state.holdings.summary;
  document.querySelector("#overview").innerHTML = `
    <div class="metric-grid">
      ${metric("候选数量", today.candidate_count, "来自今日规则扫描")}
      ${metric("持仓数量", holdings.holding_count, "当前 Mock 持仓")}
      ${metric("组合今日涨跌", pct(holdings.portfolio_change_pct), "按持仓市值加权")}
      ${metric("MA20上方", holdings.above_ma20_count, `${holdings.below_ma20_count} 只位于 MA20 下方`)}
    </div>
    <section class="panel">
      <h2>今日关注</h2>
      <p class="panel-note">${today.market_note}</p>
    </section>
    <section>
      <h2>候选摘录</h2>
      <div class="card-grid">
        ${state.today.candidates.slice(0, 2).map((stock) => stockCard(stock)).join("") || "<p class=\"panel-note\">今日没有满足规则的候选。</p>"}
      </div>
    </section>
  `;
};

const renderCandidates = () => {
  document.querySelector("#candidates").innerHTML = `
    <div class="card-grid">
      ${state.today.candidates.map((stock) => stockCard(stock)).join("") || "<section class=\"panel\"><p class=\"panel-note\">今日没有满足缩量回踩与 MA20 条件的候选。</p></section>"}
    </div>
  `;
};

const renderHoldings = () => {
  document.querySelector("#holdings").innerHTML = `
    <section class="panel">
      <h2>持仓概览</h2>
      <table class="data-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>收盘</th>
            <th>今日涨跌</th>
            <th>MA20距离</th>
            <th>量比5日</th>
            <th>板块相对</th>
            <th>标签</th>
          </tr>
        </thead>
        <tbody>
          ${state.holdings.holdings.map((stock) => `
            <tr>
              <td><strong>${stock.name}</strong><br><span class="industry">${stock.code} · ${stock.industry}</span></td>
              <td>${number(stock.close)}</td>
              <td class="${changeClass(stock.change_pct)}">${pct(stock.change_pct)}</td>
              <td>${pct(stock.ma20_distance)}</td>
              <td>${number(stock.volume_ratio_5d)}</td>
              <td class="${changeClass(stock.relative_industry_strength)}">${pct(stock.relative_industry_strength)}</td>
              <td>${tags(stock.labels)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </section>
  `;
};

const renderReview = () => {
  const summary = state.holdings.summary;
  document.querySelector("#review").innerHTML = `
    <div class="metric-grid">
      ${metric("强于板块", summary.stronger_than_industry_count)}
      ${metric("弱于板块", summary.weaker_than_industry_count)}
      ${metric("缩量回踩", summary.shrink_pullback_count)}
      ${metric("放量异常", summary.abnormal_volume_count)}
    </div>
    <section class="panel">
      <h2>客观复盘</h2>
      <ul class="review-list">
        <li>${summary.review}</li>
        <li>组合今日涨跌为 ${pct(summary.portfolio_change_pct)}，当前持仓 ${summary.holding_count} 只。</li>
        <li>${summary.above_ma20_count} 只位于 MA20 上方，${summary.below_ma20_count} 只位于 MA20 下方。</li>
        <li>${summary.stronger_than_industry_count} 只强于所属板块，${summary.weaker_than_industry_count} 只弱于所属板块。</li>
      </ul>
    </section>
  `;
};

const renderWatchlist = () => {
  document.querySelector("#watchlist").innerHTML = `
    <div class="card-grid">
      ${state.watchlist.watchlist.map((item) => stockCard({
        ...item,
        close: item.current_price,
        ma20_distance: item.current_ma20_distance,
        labels: item.current_labels,
        change_pct: item.price_change_since_added,
        relative_industry_strength: item.industry_strength,
      }, `
        <div class="kv-grid">
          <div class="kv"><span>加入日期</span><strong>${item.added_date}</strong></div>
          <div class="kv"><span>加入价格</span><strong>${number(item.added_price)}</strong></div>
          <div class="kv"><span>触发条件</span><strong>${item.triggered_conditions.join(" / ")}</strong></div>
        </div>
      `)).join("")}
    </div>
  `;
};

const setView = (view) => {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("active", section.id === view);
  });
  document.querySelector("#view-title").textContent = titles[view];
};

const loadData = async () => {
  const [today, holdings, watchlist] = await Promise.all([
    fetch("../data/today.json").then((response) => response.json()),
    fetch("../data/holdings.json").then((response) => response.json()),
    fetch("../data/watchlist.json").then((response) => response.json()),
  ]);
  state.today = today;
  state.holdings = holdings;
  state.watchlist = watchlist;
  document.querySelector("#trade-date").textContent = today.summary.trade_date;
  renderOverview();
  renderCandidates();
  renderHoldings();
  renderReview();
  renderWatchlist();
};

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

loadData().catch((error) => {
  document.querySelector("#overview").innerHTML = `
    <section class="panel">
      <h2>数据加载失败</h2>
      <p class="panel-note">${error.message}</p>
    </section>
  `;
});
