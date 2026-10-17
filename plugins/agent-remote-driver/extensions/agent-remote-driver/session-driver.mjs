export function createSessionDriver(session) {
  return {
    send: ({ content, mode, source }) => session.send({
      prompt: content,
      mode,
      ...(source === undefined ? {} : { source }),
    }),
    abort: () => session.abort(),
  };
}
