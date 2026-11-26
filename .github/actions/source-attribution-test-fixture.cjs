const marker = "<!-- agent-worktrees:source codename=clear-compass -->";
const context = {
  repo: { owner: "owner", repo: "repo" }, payload: { pull_request: { number: 1, head: { sha: "head" } } },
  serverUrl: "https://github.com", runId: 123,
};

function pr(body, labels = []) {
  return { body, labels, base: { ref: "dev" }, state: "open", head: { sha: "head" } };
}

function paged(invoke) {
  invoke.iterator = async function* (method, options) {
    const values = await invoke(method, options);
    for (let start = 0; start < Math.max(1, values.length); start += options.per_page) {
      const next = start + options.per_page < values.length;
      yield { data: values.slice(start, start + options.per_page),
        headers: next ? { link: '<https://api.github.com/next>; rel="next"' } : {} };
    }
  };
  return invoke;
}

function countRequests(github) {
  let requests = 0;
  let paginationRoute;
  for (const endpoints of Object.values(github.rest)) {
    for (const [name, invoke] of Object.entries(endpoints)) {
      const operation = async (...params) => {
        if (paginationRoute !== operation) requests++;
        return invoke(...params);
      };
      endpoints[name] = operation;
    }
  }
  const paginate = github.paginate;
  github.paginate = paged(async (method, options) => {
    const previous = paginationRoute;
    paginationRoute = method;
    requests++;
    try {
      const result = await paginate(method, options);
      requests += Math.max(0, Math.ceil(result.length / options.per_page) - 1);
      return result;
    } finally { paginationRoute = previous; }
  });
  return () => requests;
}

function core() {
  const messages = [];
  const summary = {
    addHeading(value) { messages.push(value); return this; },
    addRaw(value) { messages.push(value); return this; },
    async write() {},
  };
  return {
    messages, summary, setFailed: (value) => messages.push(value),
    info: (value) => messages.push(value), warning: (value) => messages.push(value),
  };
}

function headFixture(values) {
  const created = [];
  const github = {
    paginate: paged(async (method, options) => {
      if (method === github.rest.checks.listForRef) {
        if (options.filter !== "all") throw new Error("Scoped fixture requires the complete check series.");
        return (await github.rest.checks.listForRef(options)).data.check_runs;
      }
      return structuredClone(values.filter((value) =>
        value.state === "open" && value.base.ref === "dev" &&
        (!options.commit_sha || value.head.sha === options.commit_sha),
      ));
    }),
    rest: {
      repos: { listPullRequestsAssociatedWithCommit() {} },
      pulls: { list() {}, get: async ({ pull_number }) => ({
        data: structuredClone(values.find((value) => value.number === pull_number)),
      }) },
      checks: {
        listForRef: async ({ ref }) => ({ data: { check_runs: created
          .map((check, index) => ({ id: index + 1, app: { slug: "github-actions" }, ...check }))
          .filter((check) => check.head_sha === ref) } }),
        create: async (check) => { created.push(check); return { data: { id: created.length } }; },
        get: async ({ check_run_id }) => ({ data: created[check_run_id - 1] }),
        update: async (check) => {
          if (!created[check.check_run_id - 1]) throw new Error("Unknown fixture check.");
          Object.assign(created[check.check_run_id - 1], check);
        },
      },
    },
  };
  return { github, created };
}

module.exports = { marker, context, pr, paged, countRequests, core, headFixture };
