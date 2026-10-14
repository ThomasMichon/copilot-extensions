export function createSessionDriver(session) {
  return {
    send: ({ content, mode }) => session.send({ prompt: content, mode }),
    abort: () => session.abort(),
  };
}
