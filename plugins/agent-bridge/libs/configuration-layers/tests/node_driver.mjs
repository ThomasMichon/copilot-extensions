import * as sdk from '../configuration-layers.mjs';
import fs from 'node:fs';

const requests = JSON.parse(fs.readFileSync(0, 'utf8'));
const results = requests.map(request => {
  try {
    if (request.action === 'read') {
      const document = sdk.read_source(request.root, request.path, {
        optional: request.optional,
        parser: request.native_parser ? text => ({native: JSON.parse(text)}) : undefined,
      });
      return {value: document};
    }
    if (request.action === 'path')
      return {value: sdk.resolve_path(request.result, request.pointer)};
    if (request.action === 'native-invalid') {
      const invalid = [new Date(), undefined, NaN, Infinity, () => {}, 2n, new Map()];
      const cyclic = {}; cyclic.self = cyclic; invalid.push(cyclic);
      const accessor = {}; Object.defineProperty(accessor, 'key', {get() { throw Error('read'); },
        enumerable: true}); invalid.push(accessor);
      invalid.push([, 1]);
      return {value: invalid.map(value => {
        try {
          sdk.compose(request.context, [{id: 'd', tier: 'defaults', origin: 'defaults',
            values: {key: value}}]);
          return false;
        } catch (error) { return error instanceof sdk.ConfigurationError; }
      })};
    }
    return {value: sdk.compose(request.context, request.layers)};
  } catch (error) {
    if (!(error instanceof sdk.ConfigurationError)) throw error;
    return {error: true};
  }
});
process.stdout.write(JSON.stringify(results));
