class RefreshLimitError extends Error {
  constructor() {
    super("Source metadata refresh reached its request budget; retry after the rate-limit reset or refresh remaining PRs through their metadata events.");
  }
}

class RefreshBudget {
  constructor(limit) {
    if (!Number.isSafeInteger(limit) || limit < 4) throw new Error("Invalid source refresh request budget.");
    this.limit = limit;
    this.used = 0;
    this.held = 0;
  }

  async request(operation, cleanup = false) {
    if (cleanup) {
      if (!this.held) throw new RefreshLimitError();
      this.held--;
    } else if (this.used + this.held >= this.limit) throw new RefreshLimitError();
    this.used++;
    return operation();
  }

  protectPending() {
    // Preserve enough requests to fence stale success and close an unfinished check.
    if (this.used + this.held + 4 > this.limit) throw new RefreshLimitError();
    this.held += 3;
  }

  release() { this.held = 0; }

  async list(github, route, options) {
    const iterator = github.paginate.iterator(route, options)[Symbol.asyncIterator]();
    const result = [];
    try {
      while (true) {
        const page = await this.request(() => iterator.next());
        if (page.done) break;
        result.push(...page.value.data);
        if (!(page.value.headers.link || "").includes('rel="next"')) break;
      }
    } finally {
      await iterator.return?.();
    }
    return result;
  }
}

const invoke = (budget, operation, cleanup = false) =>
  budget ? budget.request(operation, cleanup) : operation();
const paginate = (budget, github, route, options) =>
  budget ? budget.list(github, route, options) : github.paginate(route, options);

module.exports = { RefreshBudget, RefreshLimitError, invoke, paginate };
