// libsodium-wrappers-sumo exposes the same API surface as libsodium-wrappers
// plus the "sumo" extra primitives (crypto_box_seal, crypto_pwhash, …). The
// community types ship under the base module name, so re-export them here —
// every function this app uses is covered by @types/libsodium-wrappers.
declare module 'libsodium-wrappers-sumo' {
  export * from 'libsodium-wrappers';
}
