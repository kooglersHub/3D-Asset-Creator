extends Node3D

@export var character_scene: PackedScene
var character: Node3D

func _ready() -> void:
	character = character_scene.instantiate()
	add_child(character)
	$Camera3D.look_at(Vector3(0, 1.0, 0))
	$CanvasLayer/Label.text = "1 Idle   2 Walk   3 Run   Space Attack   H Hit   D Death   R Reset\nGenerated knight preview"
	if "--capture" in OS.get_cmdline_user_args():
		call_deferred("_capture")

func _capture() -> void:
	var args := OS.get_cmdline_user_args()
	var destination := args[args.find("--capture") + 1]
	var clip := args[args.find("--clip") + 1] if "--clip" in args else "idle"
	character.player.play(clip, 0)
	character.player.seek(0.8, true)
	character.player.pause()
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var result := get_viewport().get_texture().get_image().save_png(destination)
	print("PREVIEW_CAPTURE_RESULT ", result)
	get_tree().quit(0 if result == OK else 1)

func _unhandled_key_input(event: InputEvent) -> void:
	if not event is InputEventKey or not event.pressed or event.echo:
		return
	match event.keycode:
		KEY_1: character.set_speed(0)
		KEY_2: character.set_speed(1.5)
		KEY_3: character.set_speed(4)
		KEY_SPACE: character.attack()
		KEY_H: character.hit()
		KEY_D: character.die()
		KEY_R: get_tree().reload_current_scene()
