import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { execFile } from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { selectionSnapshot, selectionAgent, selectionPrompt, selectionReader,
    validateSelection } from "../scripts/skill-selection-contract.mjs";

test("selection preserves exact native aliases and rejects disabled, invented and stale recommendations", () => {
    const skill = { name: "plugin:vision", commandName: "vision", description: "Author intent",
        argumentHint: "[subject]", enabled: true, userInvocable: false, source: "plugin" };
    const snapshot = selectionSnapshot([skill, { ...skill, name: "disabled", enabled: false }]);
    assert.equal(snapshot.catalog[0].argumentHint, "[subject]");
    assert.throws(() => selectionSnapshot([skill, skill]), /unique/);
    assert.throws(() => selectionSnapshot([]), /nonempty/);
    const item = { skill: skill.name, reason: "Owns authoring intent", confidence: "high", guides: [] };
    const result = { version: 1, catalogDigest: snapshot.digest, status: "selected",
        recommendations: [item], uncertainties: [] };
    assert.deepEqual(validateSelection(result, snapshot), result);
    for (const name of ["vision", "disabled", "invented"]) {
        assert.throws(() => validateSelection({ ...result, recommendations: [{ ...item, skill: name }] }, snapshot));
    }
    assert.throws(() => validateSelection({ ...result, catalogDigest: "old" }, snapshot), /stale/);
    assert.throws(() => validateSelection({ ...result, recommendations: [item, item] }, snapshot));
    assert.throws(() => validateSelection({ ...result, authority: "approved" }, snapshot));
    assert.throws(() => validateSelection({ ...result, recommendations: [{ ...item, guides: ["invented.md"] }] }, snapshot));
    assert.throws(() => validateSelection({ ...result, status: "no-match" }, snapshot));
    assert.throws(() => validateSelection({ ...result, status: "selected", recommendations: [] }, snapshot));
    assert.throws(() => validateSelection({ ...result, status: "insufficient-context" }, snapshot));
    assert.equal(validateSelection({ ...result, status: "no-match", recommendations: [] }, snapshot).status, "no-match");
});
test("canonical specialist uses native configuration without changing the caller or inventing task parameters", () => {
    const profile = { model: "explicit-fixture-model", reasoningEffort: "medium" };
    const agent = selectionAgent(profile);
    assert.equal(agent.model, profile.model);
    assert.equal(agent.reasoningEffort, "medium");
    assert.deepEqual(agent.tools, ["skill_selection_read"]);
    assert.equal(agent.infer, false);
    assert(!Object.hasOwn(agent, "contextTier") && !Object.hasOwn(agent, "skills"));
    assert(!Object.hasOwn(selectionAgent(), "model"));
    assert.throws(() => selectionAgent({ reasoningEffort: "medium" }), /profile/);
    const snapshot = selectionSnapshot([{ name: "fixture", description: "Fixture", enabled: true, source: "provider" }]);
    const prompt = JSON.parse(selectionPrompt(snapshot, { intent: "Review the current contract", purpose: "review",
        context: { messages: [], omitted: 3, truncated: true } }));
    assert.equal(prompt.catalogDigest, snapshot.digest);
    assert.equal(prompt.context.omitted, 3);
    assert.equal(prompt.catalog[0].name, "fixture");
    assert.throws(() => selectionPrompt(snapshot, { intent: "Review", purpose: "invented", context: {} }));
});
test("declared-source reads admit contained parent references without crawling or invoking a skill", async () => {
    const root = await fs.mkdtemp(join(tmpdir(), "skill-selection-contract-"));
    try {
        const skillRoot = join(root, "skills", "fixture");
        await fs.mkdir(skillRoot, { recursive: true });
        await fs.mkdir(join(root, "references"));
        const body = "[Guide](../../references/guide.md)\n[Remote](https://example.invalid/a.md)\n";
        await fs.writeFile(join(skillRoot, "SKILL.md"), body);
        await fs.writeFile(join(root, "references", "guide.md"), "Declared guidance");
        const snapshot = selectionSnapshot([{ name: "fixture", description: "Fixture", enabled: true,
            source: "project", path: join(skillRoot, "SKILL.md") },
        { name: "pathless", description: "Advisory", enabled: true, source: "provider" }]);
        const reader = await selectionReader(snapshot, [root]);
        const source = await reader.read({ skill: "fixture" });
        assert.deepEqual(source.references, ["../../references/guide.md", "https://example.invalid/a.md"]);
        const item = { skill: "fixture", reason: "Owns the procedure", confidence: "high", guides: [source.references[0]] };
        const result = { version: 1, catalogDigest: snapshot.digest, status: "selected",
            recommendations: [item], uncertainties: [] };
        assert.throws(() => validateSelection(result, snapshot, reader.receipts()), /successful/);
        assert.equal((await reader.read({ skill: "fixture", reference: source.references[0] })).content, "Declared guidance");
        assert.deepEqual(validateSelection(result, snapshot, reader.receipts()), result);
        assert.deepEqual(await reader.validate(result), result);
        assert.throws(() => validateSelection(result, snapshot,
            reader.receipts().map(receipt => ({ ...receipt, catalogDigest: "other catalog" }))), /successful/);
        await fs.writeFile(join(root, "references", "guide.md"), "Changed guidance");
        await assert.rejects(reader.validate(result), /changed before/);
        await assert.rejects(reader.read({ skill: "fixture", reference: source.references[0] }), /source changed/);
        assert.equal((await reader.read({ skill: "pathless" })).status, "unavailable");
        await assert.rejects(reader.read({ skill: "fixture", reference: "invented.md" }), /not declared/);
        await assert.rejects(reader.read({ skill: "fixture", reference: source.references[1] }), /local relative/);
        await fs.writeFile(join(skillRoot, "SKILL.md"), "[Escape](../../../outside.md)");
        await assert.rejects(reader.read({ skill: "fixture" }), /source changed/);
        const fresh = await selectionReader(snapshot, [root]);
        await assert.rejects(fresh.read({ skill: "fixture", reference: "../../../outside.md" }), /outside admitted/);
        const limited = await selectionReader(snapshot, [root], { maxBytes: 2, maxReads: 1 });
        await assert.rejects(limited.read({ skill: "fixture" }), /byte limit/);
        await assert.rejects(limited.read({ skill: "fixture" }), /allowance/);
        const denied = await selectionReader(snapshot, []);
        await assert.rejects(denied.read({ skill: "fixture" }), /outside admitted/);
    } finally { await fs.rm(root, { recursive: true, force: true }); }
});
test("selection source boundary rejects symlink escapes, encoded absolute paths and invalid text", async () => {
    const root = await fs.mkdtemp(join(tmpdir(), "skill-selection-boundary-"));
    try {
        const admitted = join(root, "admitted"), outside = join(root, "outside");
        await fs.mkdir(admitted); await fs.mkdir(outside);
        await fs.symlink(outside, join(admitted, "escape"), "junction");
        await fs.writeFile(join(outside, "guide.md"), "Must not be read");
        const path = join(admitted, "SKILL.md");
        const skill = { name: "fixture", description: "Fixture", enabled: true, source: "project", path };
        await fs.writeFile(path, "[Escape](escape/guide.md)\n[Encoded](%2Foutside.md)\n[Drive](C%3Aguide.md)");
        const snapshot = selectionSnapshot([skill]);
        const reader = await selectionReader(snapshot, [admitted]);
        await assert.rejects(reader.read({ skill: "fixture", reference: "escape/guide.md" }), /escapes admitted/);
        await assert.rejects(reader.read({ skill: "fixture", reference: "%2Foutside.md" }), /local relative/);
        await assert.rejects(reader.read({ skill: "fixture", reference: "C%3Aguide.md" }), /local relative/);
        assert.deepEqual(reader.receipts(), []);
        await fs.writeFile(path, Buffer.from([0xff, 0xfe]));
        await assert.rejects(reader.read({ skill: "fixture" }), /encoded data/);
    } finally { await fs.rm(root, { recursive: true, force: true }); }
});
test("Markdown declarations exclude examples and preserve balanced and escaped destinations", async () => {
    const root = await fs.mkdtemp(join(tmpdir(), "skill-selection-markdown-"));
    try {
        const path = join(root, "SKILL.md");
        await fs.writeFile(path, [
            "`[Example](wrong.md)`", "\\[Escaped](wrong.md)", "```md", "[Fence](wrong.md)", "````",
            "    [Indented code](wrong.md)", "<!-- [Comment](wrong.md) -->",
            "> ```", "> [Quoted code](wrong.md)", "> ```",
            "- ```md", "  [List code](wrong.md)", "  ```", "[After list](after.md)",
            "[Guide](api(v1).md)", "[Escaped target](api\\(v2\\).md)",
            "[Angle](<guide with spaces.md> 'title')", "[ref]: ref(v3).md",
            '[Title](real.md "See [example](wrong.md)")',
            "[Malformed](api(v4).md junk)", "[Unclosed](api(v5.md)",
        ].join("\n"));
        const expected = ["after.md", "api(v1).md", "api(v2).md", "guide with spaces.md", "ref(v3).md", "real.md"];
        for (const name of expected) {
            await fs.writeFile(join(root, name), name);
        }
        const reader = await selectionReader(selectionSnapshot([
            { name: "fixture", description: "Fixture", source: "project", enabled: true, path },
        ]), [root]);
        const source = await reader.read({ skill: "fixture" });
        assert.deepEqual(source.references, expected);
        for (const reference of source.references) {
            assert.equal((await reader.read({ skill: "fixture", reference })).content, reference);
        }
        await assert.rejects(reader.read({ skill: "fixture", reference: "wrong.md" }), /not declared/);
        await assert.rejects(reader.read({ skill: "fixture", reference: "api(v1" }), /not declared/);
    } finally { await fs.rm(root, { recursive: true, force: true }); }
});
test("bracket-heavy malformed Markdown retains valid later declarations with bounded parsing work", async () => {
    const root = await fs.mkdtemp(join(tmpdir(), "skill-selection-brackets-"));
    try {
        const path = join(root, "SKILL.md");
        await fs.writeFile(path, "[".repeat(25000) + "\n[Later](later.md)");
        const reader = await selectionReader(selectionSnapshot([
            { name: "fixture", description: "Fixture", source: "project", enabled: true, path },
        ]), [root]);
        assert.deepEqual((await reader.read({ skill: "fixture" })).references, ["later.md"]);
    } finally { await fs.rm(root, { recursive: true, force: true }); }
});
test("supplied root aliases work and source-target rebinding invalidates identical skill text", async () => {
    const root = await fs.mkdtemp(join(tmpdir(), "skill-selection-rebinding-"));
    try {
        const first = join(root, "first"), second = join(root, "second"), alias = join(root, "alias");
        await fs.mkdir(first); await fs.mkdir(second);
        for (const directory of [first, second]) await fs.writeFile(join(directory, "SKILL.md"), "[Guide](guide.md)");
        await fs.writeFile(join(first, "guide.md"), "First guide");
        await fs.writeFile(join(second, "guide.md"), "Second guide");
        await fs.symlink(first, alias, "junction");
        const snapshot = selectionSnapshot([
            { name: "fixture", description: "Fixture", source: "project", enabled: true, path: join(alias, "SKILL.md") },
        ]);
        const aliased = await selectionReader(snapshot, [alias]);
        assert.equal((await aliased.read({ skill: "fixture", reference: "guide.md" })).content, "First guide");
        const reader = await selectionReader(snapshot, [root]);
        await reader.read({ skill: "fixture", reference: "guide.md" });
        const result = { version: 1, catalogDigest: snapshot.digest, status: "selected", uncertainties: [],
            recommendations: [{ skill: "fixture", reason: "Owns procedure", confidence: "high", guides: ["guide.md"] }] };
        assert.deepEqual(await reader.validate(result), result);
        await fs.unlink(alias);
        await fs.symlink(second, alias, "junction");
        await assert.rejects(reader.validate(result), /changed before/);
        await assert.rejects(reader.read({ skill: "fixture", reference: "guide.md" }), /source changed/);
    } finally { await fs.rm(root, { recursive: true, force: true }); }
});
test("overlapping guide acquisition cannot record receipts from two skill revisions", async t => {
    const root = await fs.mkdtemp(join(tmpdir(), "skill-selection-overlap-"));
    let release, entered;
    const held = new Promise(resolve => { release = resolve; });
    const gate = new Promise(resolve => { entered = resolve; });
    let first;
    try {
        const path = join(root, "SKILL.md"), guide = join(root, "guide.md");
        await fs.writeFile(path, "[Guide](guide.md)");
        await fs.writeFile(guide, "Guide");
        const reader = await selectionReader(selectionSnapshot([
            { name: "fixture", description: "Fixture", source: "project", enabled: true, path },
        ]), [root]);
        const actual = fs.realpath;
        t.mock.method(fs, "realpath", async (...args) => {
            if (args[0] === guide) { entered(); await held; }
            return actual(...args);
        });
        syncBuiltinESMExports();
        first = reader.read({ skill: "fixture", reference: "guide.md" });
        await Promise.race([gate, new Promise((_, reject) => setTimeout(() => reject(new Error("acquisition gate missed")), 2000))]);
        await fs.writeFile(path, "[Guide](guide.md)\nNew revision");
        await assert.rejects(reader.read({ skill: "fixture", reference: "guide.md" }), /source changed/);
        release();
        await first;
        assert.equal(reader.receipts().length, 1);
    } finally {
        release();
        if (first) await first;
        t.mock.restoreAll(); syncBuiltinESMExports();
        await fs.rm(root, { recursive: true, force: true });
    }
});
test("Unix FIFO sources are refused in a bounded child instead of blocking file acquisition", async () => {
    if (process.platform === "win32") return;
    const root = await fs.mkdtemp(join(tmpdir(), "skill-selection-fifo-"));
    try {
        const path = join(root, "SKILL.md");
        await promisify(execFile)("mkfifo", [path], { timeout: 2000, windowsHide: true });
        const script = `import {selectionSnapshot,selectionReader} from ${JSON.stringify(new URL("../scripts/skill-selection-contract.mjs", import.meta.url).href)};
            const reader = await selectionReader(selectionSnapshot([{name:"fixture",description:"Fixture",source:"project",enabled:true,path:${JSON.stringify(path)}}]),[${JSON.stringify(root)}]);
            try { await reader.read({skill:"fixture"}); process.exitCode=1; }
            catch(error) { if (!error.message.includes("not a regular file")) throw error; }`;
        await promisify(execFile)(process.execPath, ["--input-type=module", "-e", script],
            { timeout: 3000, windowsHide: true });
    } finally { await fs.rm(root, { recursive: true, force: true }); }
});
