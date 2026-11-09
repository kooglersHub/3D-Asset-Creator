"""Pinned candidates selected from official model cards, not a claimed benchmark winner."""

MODELS = {
    "qwen-edit": {
        "repo": "Qwen/Qwen-Image-Edit-2511",
        "revision": "6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9",
        "license": "Apache-2.0",
        "role": "pose and reference editing",
    },
    "flux-klein": {
        "repo": "black-forest-labs/FLUX.2-klein-4B",
        "revision": "e7b7dc27f91deacad38e78976d1f2b499d76a294",
        "license": "Apache-2.0",
        "role": "faster reference editing alternative",
    },
    "birefnet": {
        "repo": "ZhengPeng7/BiRefNet",
        "revision": "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4",
        "license": "MIT",
        "role": "background removal",
    },
    "trellis2": {
        "repo": "microsoft/TRELLIS.2-4B",
        "revision": "af44b45f2e35a493886929c6d786e563ec68364d",
        "license": "MIT; DINOv3 encoder has separate terms",
        "role": "coarse geometry and PBR texture",
    },
    "hi3d": {
        "repo": "hi3d-official/Hi3Dv1.0",
        "revision": "d502e938cbedc6e87aba8884614c5ca6d177a4b6",
        "license": "see pinned upstream license; attribution required",
        "role": "optional geometry-only refinement",
    },
}
DIFFUSERS_VERSION = "0.37.0"
TRELLIS_REVISION = "75fbf0183001ed9876c8dbb35de6b68552ee08bd"

FRONT_PROMPT = (
    "Edit this exact single character into a full-body orthographic front-view character "
    "reference. Preserve the armor, helmet, proportions and recognizable design. "
    "A relaxed symmetrical A-pose: arms about 45 degrees away from the torso, "
    "open empty hands, separated fingers and separated legs, both feet on the ground. "
    "The character holds nothing. Reconstruct the hands where equipment was held. "
    "Use soft even neutral lighting and a plain white background. "
    "Keep the complete head and feet inside the picture with margins."
)
BACK_PROMPT = (
    "Reference image 1 is the original design; reference image 2 is the approved-style "
    "front reference. Show the SAME single character from directly behind in the SAME "
    "A-pose, with matching proportions, armor, scale, framing and foot positions. "
    "Both hands are empty. Soft even neutral lighting, plain white background."
)
WEAPON_PROMPT = (
    "Extract the character's main sword as ONE isolated complete straight sword. "
    "Preserve its recognizable blade, guard, grip and pommel. Center it vertically, "
    "blade pointing up, grip at the bottom. Show the full silhouette with margins, "
    "plain white background, soft neutral light. Only the weapon is visible."
)
