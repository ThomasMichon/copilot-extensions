import { boundedJson, CaptureError, identifier, projectQuota } from "./quota-projection.mjs";

function origin(value) {
    if (typeof value !== "string" || value.length > 300 || /[\x00-\x20\x7f]/u.test(value)) {
        throw new CaptureError("account-origin-invalid");
    }
    if (!/^https:\/\/[a-z0-9.-]+(?::443)?\/?$/iu.test(value)) {
        throw new CaptureError("account-origin-invalid");
    }
    let parsed;
    try {
        parsed = new URL(value);
    } catch {
        throw new CaptureError("account-origin-invalid");
    }
    if (parsed.protocol !== "https:" || parsed.username || parsed.password
        || !/^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$/u.test(parsed.hostname)
        || parsed.hostname.includes("..") || parsed.port
        || !["", "/"].includes(parsed.pathname) || parsed.search || parsed.hash) {
        throw new CaptureError("account-origin-invalid");
    }
    return parsed.origin;
}

function options(args) {
    if (args === null || typeof args !== "object" || Array.isArray(args)) {
        throw new CaptureError("arguments-invalid");
    }
    if (Object.keys(args).some((key) => ![
        "timeoutSeconds", "category", "providerId", "quotaId", "expectedLogin", "expectedHost",
    ].includes(key))) throw new CaptureError("arguments-invalid");
    const timeout = args.timeoutSeconds === undefined ? 10 : args.timeoutSeconds;
    if (typeof timeout !== "number" || !Number.isFinite(timeout) || timeout <= 0 || timeout > 30) {
        throw new CaptureError("deadline-invalid");
    }
    const category = args.category === undefined ? "premium_interactions" : args.category;
    if (!identifier(category) || [args.providerId, args.quotaId].some(
        (value) => value !== undefined && !identifier(value),
    ) || (args.quotaId !== undefined && args.providerId === undefined)) {
        throw new CaptureError("quota-selector-invalid");
    }
    const account = args.expectedLogin !== undefined || args.expectedHost !== undefined;
    if (account && (typeof args.expectedLogin !== "string"
        || !/^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,37}[A-Za-z0-9])?$/u.test(args.expectedLogin))) {
        throw new CaptureError("account-login-invalid");
    }
    return {
        ...args, category, timeout, account,
        host: account ? origin(args.expectedHost) : undefined,
    };
}

function failure(diagnostic, status = "unavailable") {
    return {
        status, diagnostics: [diagnostic], capture: null,
        raw_reset: "unsupported", freshness: "unknown",
        operator_launch_blocked: false, worker_mutation: false,
    };
}

export function createQuotaTool(getSession) {
    let pending = false;
    return {
        name: "budget_guidance_session_quota",
        description: "Read only this foreground session's public quota projection. Returns a filtered "
            + "offline capture, not live budget arithmetic. No refresh or account fallback.",
        parameters: {
            type: "object", additionalProperties: false,
            properties: {
                timeoutSeconds: { type: "number", exclusiveMinimum: 0, maximum: 30, default: 10 },
                category: { type: "string", maxLength: 256 },
                providerId: { type: "string", maxLength: 256 },
                quotaId: { type: "string", maxLength: 256 },
                expectedLogin: { type: "string", maxLength: 39 },
                expectedHost: { type: "string", maxLength: 300 },
            },
        },
        async handler(args, invocation) {
            const respond = (value) => ({
                textResultForLlm: boundedJson(value),
                resultType: value.status === "captured" ? "success" : "failure",
            });
            if (pending) return respond(failure("quota-acquisition-already-pending"));
            let config;
            try {
                config = options(args);
            } catch (error) {
                return respond(failure(error instanceof CaptureError
                    ? "quota-arguments-invalid" : "quota-arguments-error", "error"));
            }
            const session = getSession();
            const sessionId = invocation?.sessionId;
            if (!identifier(sessionId) || session?.sessionId !== sessionId) {
                return respond(failure("requesting-session-missing-or-mismatch"));
            }
            const rpc = session.rpc;
            const quota = rpc?.quota;
            const getter = quota?.get;
            const auth = config.account ? rpc?.gitHubAuth : undefined;
            const statusGetter = auth?.getStatus;
            if (typeof getter !== "function") return respond(failure("session-quota-rpc-unsupported"));
            if (config.account && typeof statusGetter !== "function") {
                return respond(failure("session-github-auth-status-unsupported"));
            }
            let expired = false;
            let timer;
            const endsAt = performance.now() + config.timeout * 1000;
            const check = () => {
                if (expired || performance.now() >= endsAt) {
                    throw new CaptureError("quota-acquisition-timeout");
                }
                if (getSession() !== session || session.sessionId !== sessionId || session.rpc !== rpc
                    || rpc.quota !== quota || quota.get !== getter
                    || (config.account && (rpc.gitHubAuth !== auth || auth.getStatus !== statusGetter))) {
                    throw new CaptureError("requesting-session-changed-during-acquisition");
                }
            };
            const checkIdentity = async () => {
                check();
                const status = await statusGetter.call(auth);
                check();
                boundedJson(status);
                if (status?.isAuthenticated !== true || typeof status.login !== "string"
                    || status.login.toLowerCase() !== config.expectedLogin.toLowerCase()
                    || origin(status.host) !== config.host) {
                    throw new CaptureError("requesting-account-missing-or-mismatch");
                }
            };
            pending = true;
            const acquire = async () => {
                try {
                    check();
                    if (config.account) await checkIdentity();
                    check();
                    const raw = await getter.call(quota);
                    check();
                    if (config.account) await checkIdentity();
                    check();
                    const projection = projectQuota(raw, config);
                    check();
                    const result = {
                        status: "captured", attribution: "foreground-session-capture",
                        freshness: "unknown", raw_reset: "unsupported",
                        diagnostics: ["capture-remains-caller-declared",
                            "raw-legacy-reset-unsupported", "provider-freshness-not-inferred"],
                        capture: {
                            schema: "copilot-extensions.copilot-quota-capture", version: 1,
                            session_id: sessionId, runtime_version: "unknown",
                            acquired_at: new Date().toISOString(), projection,
                        },
                    };
                    if (config.account) {
                        result.account_identity = {
                            status: "matched", scope: "session-github-auth",
                            login: config.expectedLogin, host: config.host, quota_binding: "not-atomic",
                        };
                    }
                    const response = respond(result);
                    check();
                    return response;
                } catch (error) {
                    return respond(failure(error instanceof CaptureError ? error.message
                        : "quota-acquisition-error", "error"));
                } finally {
                    // A timed-out public RPC may still be pending; never replace it with another.
                    pending = false;
                }
            };
            try {
                const deadline = new Promise((resolve) => {
                    timer = setTimeout(() => {
                        expired = true;
                        resolve(respond(failure("quota-acquisition-timeout", "error")));
                    }, config.timeout * 1000);
                });
                return await Promise.race([acquire(), deadline]);
            } finally {
                clearTimeout(timer);
            }
        },
    };
}
