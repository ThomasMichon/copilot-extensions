const MAX_BYTES = 64 * 1024;
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export class CaptureError extends Error {}

export function boundedJson(value) {
    let bytes = 0;
    const chunks = [];
    const ancestors = new Set();
    const count = (size) => {
        bytes += size;
        if (bytes > MAX_BYTES) throw new CaptureError("capture-size-limit");
    };
    const append = (text) => { count(text.length); chunks.push(text); };
    const string = (text) => {
        count(2);
        for (let index = 0; index < text.length; index++) {
            const code = text.charCodeAt(index);
            if (code === 34 || code === 92) count(2);
            else if (code < 32) count([8, 9, 10, 12, 13].includes(code) ? 2 : 6);
            else if (code >= 0xd800 && code <= 0xdbff
                && text.charCodeAt(index + 1) >= 0xdc00 && text.charCodeAt(index + 1) <= 0xdfff) {
                count(4);
                index++;
            } else if (code >= 0xd800 && code <= 0xdfff) count(6);
            else count(code < 128 ? 1 : code < 2048 ? 2 : 3);
        }
        // Only a string already proved to fit is ever materialized as JSON.
        chunks.push(JSON.stringify(text));
    };
    const encode = (item, depth) => {
        if (depth > 64) throw new CaptureError("capture-depth-limit");
        if (item === null) return append("null");
        if (typeof item === "string") return string(item);
        if (typeof item === "number" || typeof item === "boolean") {
            return append(JSON.stringify(item));
        }
        if (typeof item !== "object" || ancestors.has(item)) {
            throw new CaptureError("quota-projection-invalid");
        }
        ancestors.add(item);
        if (Array.isArray(item)) {
            append("[");
            for (let index = 0; index < item.length; index++) {
                if (index) append(",");
                encode(item[index] === undefined ? null : item[index], depth + 1);
            }
            append("]");
        } else {
            const prototype = Object.getPrototypeOf(item);
            if (prototype !== null && prototype !== Object.prototype) {
                throw new CaptureError("quota-projection-invalid");
            }
            append("{");
            let first = true;
            for (const key in item) {
                if (!Object.hasOwn(item, key)) continue;
                const member = item[key];
                if (member === undefined) continue;
                if (!first) append(",");
                first = false;
                string(key);
                append(":");
                encode(member, depth + 1);
            }
            append("}");
        }
        ancestors.delete(item);
    };
    encode(value, 0);
    return chunks.join("");
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
    observationKind: ["account_snapshot", "admission_state"],
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
