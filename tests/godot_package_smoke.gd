extends SceneTree

func _initialize() -> void:
	call_deferred("check_package")

func check_package() -> void:
	var packed = load("res://Assets/Generated/Fixture/character.tscn")
	assert(packed is PackedScene)
	var visual = packed.instantiate()
	root.add_child(visual)
	await process_frame
	assert(visual.player != null)
	assert(visual.player.has_animation("idle"))
	assert(visual.player.has_animation("sword_swing"))
	assert(visual.player.current_animation == "idle")
	var attachment = visual.find_children("*", "BoneAttachment3D", true, false)[0]
	assert(attachment.bone_name == "bone_7")
	var weapon = attachment.get_child(0)
	visual.player.advance(0.01)
	var original_position: Vector3 = weapon.global_position
	visual.attack()
	assert(visual.locked)
	assert(visual.player.current_animation == "sword_swing")
	visual.player.advance(0.2)
	await process_frame
	assert(weapon.global_position.distance_to(original_position) > 0.01)
	assert(weapon.scale.is_equal_approx(Vector3.ONE))
	visual.player.advance(2.0)
	await process_frame
	assert(not visual.locked)
	assert(visual.player.current_animation == "idle")
	visual.queue_free()
	await process_frame
	print("GODOT_PACKAGE_SMOKE_OK: animation, rigid sword attachment and idle return")
	quit(0)
