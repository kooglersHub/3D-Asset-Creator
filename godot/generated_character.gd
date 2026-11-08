extends Node3D
## Visual adapter: the game's existing controller calls these methods.
## Weapons follow a BoneAttachment3D; combat damage stays in the game controller.

@export var model_scene: PackedScene
@export var weapon_scene: PackedScene
@export var hand_bone: StringName
@export var weapon_position := Vector3.ZERO
@export var weapon_rotation_degrees := Vector3.ZERO
@export var weapon_scale := 1.0
@export var facing_degrees := 0.0
@export var clip_map := {"idle": "idle", "walk": "walk", "run": "run", "attack": "sword_swing", "hit": "hit_react", "death": "death"}
var player: AnimationPlayer
var locked := false
var dead := false
var speed := 0.0

func _ready() -> void:
	if not model_scene:
		push_error("Generated character has no model_scene")
		return
	var model := model_scene.instantiate()
	add_child(model)
	model.rotation.y = deg_to_rad(facing_degrees)
	player = _find(model, "AnimationPlayer") as AnimationPlayer
	var skeleton := _find(model, "Skeleton3D") as Skeleton3D
	if weapon_scene:
		if not skeleton or skeleton.find_bone(hand_bone) < 0:
			push_error("Choose a valid hand bone before equipping the weapon")
			return
		var attachment := BoneAttachment3D.new()
		skeleton.add_child(attachment)
		attachment.bone_name = hand_bone
		var weapon := weapon_scene.instantiate()
		attachment.add_child(weapon)
		weapon.position = weapon_position
		weapon.rotation_degrees = weapon_rotation_degrees
		weapon.scale *= weapon_scale
	if player:
		for state in clip_map:
			var clip := StringName(clip_map[state])
			if player.has_animation(clip):
				player.get_animation(clip).loop_mode = Animation.LOOP_LINEAR if state in ["idle", "walk", "run"] else Animation.LOOP_NONE
		player.animation_finished.connect(_animation_finished)
		_locomotion()

func _find(node: Node, type_name: String) -> Node:
	if node.is_class(type_name):
		return node
	for child in node.get_children():
		var found := _find(child, type_name)
		if found:
			return found
	return null

func set_speed(value: float) -> void:
	speed = maxf(value, 0.0)
	if not locked and not dead:
		_locomotion()

func attack() -> void:
	_one_shot("attack")

func hit() -> void:
	_one_shot("hit")

func die() -> void:
	dead = true
	locked = true
	_play("death")

func _one_shot(state: String) -> void:
	if not dead and _play(state):
		locked = true

func _play(state: String) -> bool:
	var clip := StringName(clip_map.get(state, ""))
	if not player or not player.has_animation(clip):
		return false
	if player.current_animation != clip or not player.is_playing():
		player.play(clip, 0.12)
	return true

func _locomotion() -> void:
	_play("run" if speed > 3.0 else "walk" if speed > 0.1 else "idle")

func _animation_finished(_clip: StringName) -> void:
	if not dead:
		locked = false
		_locomotion()
