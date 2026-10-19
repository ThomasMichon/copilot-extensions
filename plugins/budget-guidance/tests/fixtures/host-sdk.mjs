export const state = { joins: 0, calls: [], options: null, stringQuantity: false };
export async function joinSession(options) {
    state.joins++;
    state.options = options;
    const forbidden = () => { throw new Error("forbidden mutation"); };
    return {
        sessionId: "session-example",
        rpc: {
            quota: {
                async get() {
                    state.calls.push("quota");
                    return { isTbbUser: true, token: "sensitive-sentinel", snapshots: {
                        premium_interactions: { isUnlimitedEntitlement: false,
                            entitlementRequests: state.stringQuantity ? "1000" : 1000,
                            usedRequests: 250,
                            tokenBasedBilling: true, resetDateEpochMs: 1896134400000 },
                    } };
                },
                refresh: forbidden, takeWarnings: forbidden,
            },
            gitHubAuth: {
                async getStatus() {
                    state.calls.push("status");
                    return { isAuthenticated: true, login: "Example_User",
                        host: "https://github.com", token: "sensitive-sentinel" };
                },
                setCredentials: forbidden,
            },
        },
        abort: forbidden, destroy: forbidden, disconnect: forbidden,
        get connection() { return forbidden(); },
        get auth() { return forbidden(); },
    };
}
export class CopilotClient { constructor() { throw new Error("new client forbidden"); } }
