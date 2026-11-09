extends SceneTree

func _initialize() -> void:
	create_timer(20).timeout.connect(func(): push_error("Asset verification timed out"); quit(1))
	call_deferred("check_asset")

func pose(player: AnimationPlayer, skeleton: Skeleton3D, clip: String, time: float) -> void:
	player.play(clip, 0)
	player.seek(time, true)
	player.pause()
	skeleton.force_update_all_bone_transforms()
	await process_frame

func check_asset() -> void:
	var args := OS.get_cmdline_user_args()
	var identifier := args[0] if args.size() else "DarkKnight"
	var directory := "res://Assets/Generated/" + identifier
	var metadata: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(directory + "/model.json"))
	var packed: PackedScene = load(directory + "/character.tscn")
	assert(packed != null)
	var visual = packed.instantiate()
	root.add_child(visual)
	await process_frame
	var player: AnimationPlayer = visual.player
	var skeleton: Skeleton3D = visual._find(visual, "Skeleton3D")
	assert(player != null and skeleton != null)
	assert(skeleton.get_bone_count() == metadata.bones.size())
	for clip in metadata.clips:
		assert(player.has_animation(clip), "Missing clip " + clip)
		await pose(player, skeleton, clip, player.get_animation(clip).length * 0.5)
	for clip in metadata.get("in_place_clips", []):
		var points: Array[Vector3] = []
		for fraction in [0.0, 0.25, 0.5, 0.75, 0.99]:
			await pose(player, skeleton, clip, player.get_animation(clip).length * fraction)
			points.append(skeleton.global_transform * skeleton.get_bone_global_pose(0).origin)
		for point in points:
			assert(Vector2(point.x-points[0].x, point.z-points[0].z).length() < 0.002, "Locomotion drift: " + clip)
	var sword_checked := false
	if visual.weapon_scene and player.has_animation("sword_swing"):
		var attachments = visual.find_children("*", "BoneAttachment3D", true, false)
		assert(attachments.size() == 1)
		var attachment: BoneAttachment3D = attachments[0]
		assert(attachment.bone_name == visual.hand_bone)
		var weapon: Node3D = attachment.get_child(0)
		await pose(player, skeleton, "sword_swing", 0)
		var initial := weapon.global_transform
		var local_transform := weapon.transform
		await pose(player, skeleton, "sword_swing", player.get_animation("sword_swing").length * 0.5)
		assert(weapon.transform.is_equal_approx(local_transform), "Weapon deformed relative to attachment")
		assert(weapon.global_position.distance_to(initial.origin) > 0.001 or weapon.global_basis.get_rotation_quaternion().angle_to(initial.basis.get_rotation_quaternion()) > 0.01)
		visual.attack()
		player.advance(player.get_animation("sword_swing").length + 0.2)
		await process_frame
		assert(not visual.locked and player.current_animation == "idle")
		sword_checked = true
	var report := {"status": "passed", "clips": metadata.clips, "bones": skeleton.get_bone_count(),
		"hand_bone": String(visual.hand_bone), "in_place_clips": metadata.get("in_place_clips", []),
		"rigid_weapon_and_attack_return": sword_checked}
	var file := FileAccess.open("res://runtime-report.json", FileAccess.WRITE)
	file.store_string(JSON.stringify(report, "  "))
	file.close()
	visual.queue_free()
	await process_frame
	print("GENERATED_ASSET_SMOKE_OK " + JSON.stringify(report))
	quit(0)
