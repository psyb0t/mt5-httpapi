// A page with children is a category whose link is the parent page, so every
// page keeps its flat /<file>/ URL. sidebar_position orders llms.txt only.
const parent = (id, label, items) => ({
  type: "category",
  label,
  link: { type: "doc", id },
  items,
});

export default {
  docs: [
    "index",
    "installation-and-configuration",
    parent("operations", "Operations", ["multi-vm-setup"]),
    parent("rest-api", "REST API", ["market-data", "trading-and-history", "files", "compiling"]),
    parent("backtesting", "Backtesting", ["backtest-optimization"]),
    parent("chart-deployments", "Chart Deployments", ["chart-control-protocol"]),
    "signals",
    "mcp-and-agents",
    "clients-and-examples",
  ],
};
