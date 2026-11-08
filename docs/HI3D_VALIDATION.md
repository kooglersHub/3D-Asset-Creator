# First Hi3D 1.0 GPU validation

Date: 2026-11-08. Powered by Hi3D 1.0.

One authorized 512 refinement run completed on Modal H200 using the existing
unrigged knight mesh and the same transparent front image. No Hi3D hosted API,
new coarse generator, Hunyuan installation or additional resolution run was used.

## Configuration and artifacts

- Resolution: 512; scheduler steps: 25; seed: 0; sequential CPU offload: enabled.
- Source revision: `13657ad09afaa92e350b461a1cd66399e44a59c4`.
- Refiner revision: `d502e938cbedc6e87aba8884614c5ca6d177a4b6`.
- Source image SHA-256: `2a2fddb43015cd5951b66bdce3d4f6da04af5233a66fdc4b4f7b01a47ea0ceec`.
- Coarse GLB SHA-256: `ab5116007a901bc7946314a81c366d74a086636518f362adf48a0b51ddbc3fa5`.
- Refined GLB SHA-256: `ccec6f31b0ee336e69fed9cd509c74f0eab87aa7f7a1a5cc65db428fe2fc395e`.
- Coarse mesh: 974,942 triangles. Refined mesh: 999,983 triangles.
- Refined GLB: 17,975,176 bytes. Export ZIP: 36,583,526 bytes.

The ZIP CRC check and all three manifest file hashes passed. Blender 5.2 imported
both models and rendered four clay views of each. Binary assets and local logs
remain outside the source repository.

## Setup findings

The initial source compilation of FlashAttention was stopped. The matching
official FlashAttention 2.7.4.post1 CUDA 12 / PyTorch 2.5 / Python 3.10 wheel
installed successfully instead. A Windows-host path serialization error in the
cache environment and volume mapping was corrected by retaining POSIX strings.
The final image built and all three pinned model components downloaded.

## Visual assessment and limits

The single reference produced recognizably similar geometry. Some armor edges
are more pronounced, while cloth and the cape are noisier. This run does not
establish an overall visual improvement or a generator benchmark.

The result remains an untextured, unrigged high-detail mesh in normalized space.
It is not a game-ready character. Texture transfer, scale/alignment, cleanup,
retopology and fresh model/motion reviews are still needed. The test does not
validate 1024/1536, other characters or other GPU configurations.
