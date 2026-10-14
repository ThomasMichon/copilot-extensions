import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

class ProbeError extends Error {}

export async function probeSendRejection(descriptor) {
  if (
    descriptor?.host !== "127.0.0.1" ||
    !Number.isInteger(descriptor.port) ||
    descriptor.port < 1 ||
    descriptor.port > 65535 ||
    typeof descriptor.token !== "string" ||
    !descriptor.token
  ) {
    throw new ProbeError("invalid loopback driver descriptor");
  }

  const scheme = "Bear" + "er ";
  const authorization = scheme + descriptor.token;
  const response = await fetch(`http://127.0.0.1:${descriptor.port}/send`, {
    method: "POST",
    headers: { authorization, "content-type": "application/json" },
    body: "{}",
    redirect: "error",
    signal: AbortSignal.timeout(5_000),
  });
  if (response.status !== 400) {
    throw new ProbeError(`expected HTTP 400 content-validation rejection; received HTTP ${response.status}`);
  }
  const body = await response.json();
  if (body.ok !== false || body.error !== "content (string) is required") {
    throw new ProbeError("expected missing-content validation error");
  }
  return { status: response.status, error: body.error };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 3) {
    console.error("usage: send-rejection-probe.mjs <descriptor-path>");
    process.exitCode = 2;
  } else {
    try {
      const descriptor = JSON.parse(await readFile(process.argv[2], "utf8"));
      await probeSendRejection(descriptor);
      console.log("PASS: authenticated /send rejects {} with HTTP 400; no prompt delivery exercised");
    } catch (error) {
      const reason = error instanceof ProbeError ? error.message
        : error?.code === "ENOENT" ? "descriptor file not found"
          : error instanceof SyntaxError ? "invalid descriptor or response JSON"
            : ["AbortError", "TimeoutError"].includes(error?.name) ? "request timed out"
              : "descriptor read or network request failed";
      console.error(`FAIL: /send rejection probe: ${reason}`);
      process.exitCode = 1;
    }
  }
}
