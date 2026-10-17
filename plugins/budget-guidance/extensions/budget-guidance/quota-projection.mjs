const MAX_BYTES = 64 * 1024;
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export class CaptureError extends Error {}

export function boundedJson(value) {
    const text = JSON.stringify(value);
    if (typeof text !== "string" || Buffer.byteLength(text, "utf8") > MAX_BYTES) {
        throw new CaptureError("capture-size-limit");
    }
    return text;
}

export function identifier(value) {
    return typeof value === "string" && value.length > 0 && value.length <= 256
        && !/[\x00-\x20\x7f]/u.test(value);
}

function scalar(value, kind) {
    if (kind === "boolean") return typeof value === "boolean";
    if (kind === "quantity") {
        return typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 1e18;
    }
    if (kind === "instant") {
        return typeof value === "string" && value.length <= 40
            && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/u.test(value)
            && Number.isFinite(Date.parse(value));
    }
    if (kind === "id") return identifier(value);
    return Array.isArray(kind) && kind.includes(value);
}

function fields(source, schema) {
    if (!object(source)) throw new CaptureError("quota-projection-invalid");
    const result = {};
    for (const [key, kind] of Object.entries(schema)) {
        if (!Object.hasOwn(source, key)) continue;
        const value = source[key];
        if (value === null) {
            result[key] = null;
        } else if (scalar(value, kind)) {
            result[key] = value;
        } else {
            throw new CaptureError("quota-projection-invalid");
        }
    }
    return result;
}

const snapshotFields = {
    isUnlimitedEntitlement: "boolean", entitlementRequests: "quantity",
    usedRequests: "quantity", tokenBasedBilling: "boolean", hasQuota: "boolean",
    overage: "quantity", resetDateEpochMs: "quantity", resetDateEstimated: "boolean",
};
const providerFields = {
    quotaId: "id", compatibilityKey: "id",
    acquisitionStatus: ["succeeded", "unavailable", "failed"],
    observationKind: ["account_snapshot", "admission_state", "unknown"],
    observedAt: "instant",
    accessState: ["allowed", "denied", "not_required", "unknown", "unavailable"],
    capacityState: ["available", "exhausted", "unlimited", "not_required",
        "not_applicable", "unknown", "unavailable"],
    quantityKind: ["authoritative_budget", "advisory_balance", "none"],
    unit: ["ai_credits", "requests", "tokens", "unknown"],
    hasQuota: "boolean", entitledQuantity: "quantity", availableQuantity: "quantity",
};
const metadataFields = {
    entitlement: "quantity", consumed: "quantity", overage: "quantity",
    unlimited: "boolean", resetAtEpochMs: "quantity", resetEstimated: "boolean",
};
const monthlyFields = {
    state: ["available", "no_policy", "unavailable", "unknown"],
    scope: ["user", "unknown"], unit: ["ai_credits", "requests", "tokens", "unknown"],
    consumedQuantity: "quantity", cycleStart: "instant", queriedAt: "instant", resetOn: "instant",
};

export function projectQuota(raw, { category, providerId, quotaId }) {
    // Bound even metadata that will be discarded, without ever rendering it.
    boundedJson(raw);
    const projection = fields(raw, { isTbbUser: "boolean" });
    if (raw.snapshots !== undefined && raw.snapshots !== null) {
        if (!object(raw.snapshots)) throw new CaptureError("quota-projection-invalid");
        const snapshot = raw.snapshots[category];
        if (snapshot !== undefined && snapshot !== null) {
            projection.snapshots = { [category]: fields(snapshot, snapshotFields) };
        }
    }
    if (providerId !== undefined) {
        if (raw.providerQuotas !== undefined && !Array.isArray(raw.providerQuotas)) {
            throw new CaptureError("quota-projection-invalid");
        }
        const candidates = (raw.providerQuotas ?? []).filter((item) =>
            object(item) && object(item.provider) && item.provider.id === providerId
            && (quotaId === undefined || item.quotaId === quotaId));
        if (candidates.length > 1) throw new CaptureError("provider-quota-ambiguous");
        projection.providerQuotas = candidates.map((item) => {
            const selected = { provider: { id: providerId }, ...fields(item, providerFields) };
            for (const [key, schema] of [
                ["budgetMetadata", metadataFields], ["monthlyUsage", monthlyFields],
            ]) {
                if (item[key] !== undefined) {
                    selected[key] = item[key] === null ? null : fields(item[key], schema);
                }
            }
            return selected;
        });
    }
    return projection;
}
