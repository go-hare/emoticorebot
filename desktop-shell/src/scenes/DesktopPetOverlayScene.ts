import Phaser from "phaser";
import { appWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/tauri";
import {
  OVERLAY_INTERACTION_LOCK_EVENT,
  dispatchOverlayInteractionLockState,
  dispatchOverlayPetActivate,
  dispatchOverlayPetHoverState,
  dispatchOverlayPetUiState,
} from "../lib/overlayPetUi";
import type { ISpriteConfig } from "../types/ISpriteConfig";

type OverlayPet = Phaser.Types.Physics.Arcade.SpriteWithDynamicBody & {
  direction?: Direction;
  availableStates: string[];
  canPlayRandomState: boolean;
  canRandomFlip: boolean;
};

type WorldBounding = {
  up: boolean;
  down: boolean;
  left: boolean;
  right: boolean;
};

enum Direction {
  UP = "UP",
  DOWN = "DOWN",
  LEFT = "LEFT",
  RIGHT = "RIGHT",
  UPSIDELEFT = "UPSIDELEFT",
  UPSIDERIGHT = "UPSIDERIGHT",
  UNKNOWN = "UNKNOWN",
}

class OverlayInputManager {
  private readonly scene: Phaser.Scene;
  private isIgnoreCursorEvents = false;
  private isMouseOverPet = false;
  private interactionLocked = false;
  private readonly ignoreDelayMs = 50;
  private lastMouseX: number | null = null;
  private lastMouseY: number | null = null;
  private lastMouseMoveAt = 0;
  private readonly hoverActivationWindowMs = 220;
  private readonly mouseMoveThreshold = 1;

  constructor(scene: Phaser.Scene) {
    this.scene = scene;
  }

  checkIsMouseOnPet(): void {
    void invoke<{ clientX?: number; clientY?: number } | null>("get_mouse_position")
      .then((position) => {
        if (!position || typeof position.clientX !== "number" || typeof position.clientY !== "number") {
          return;
        }

        const now = Date.now();
        if (this.didMouseMove(position.clientX, position.clientY)) {
          this.lastMouseMoveAt = now;
        }

        const isMouseOverPet = this.detectMouseOverPet(position.clientX, position.clientY);
        this.updateHoverState(isMouseOverPet);

        if (this.interactionLocked) {
          this.turnOffIgnoreCursorEvents();
          return;
        }

        const shouldCaptureCursor =
          isMouseOverPet &&
          (!this.isIgnoreCursorEvents || now - this.lastMouseMoveAt <= this.hoverActivationWindowMs);

        if (shouldCaptureCursor) {
          this.turnOffIgnoreCursorEvents();
          return;
        }

        this.turnOnIgnoreCursorEvents();
      })
      .catch(() => {});
  }

  turnOffIgnoreCursorEvents(): void {
    if (this.isIgnoreCursorEvents) {
      void appWindow.setIgnoreCursorEvents(false).then(() => {
        this.isIgnoreCursorEvents = false;
      });
    }
  }

  turnOnIgnoreCursorEvents(): void {
    if (!this.isIgnoreCursorEvents && !this.interactionLocked) {
      window.setTimeout(() => {
        void appWindow.setIgnoreCursorEvents(true).then(() => {
          this.isIgnoreCursorEvents = true;
        });
      }, this.ignoreDelayMs);
    }
  }

  setInteractionLocked(locked: boolean): void {
    this.interactionLocked = locked;
    if (locked) {
      this.turnOffIgnoreCursorEvents();
      return;
    }

    if (!this.isMouseOverPet) {
      this.turnOnIgnoreCursorEvents();
    }
  }

  private detectMouseOverPet(clientX: number, clientY: number): boolean {
    const input = this.scene.input;
    input.mousePointer.x = clientX / window.devicePixelRatio;
    input.mousePointer.y = clientY / window.devicePixelRatio;
    return input.hitTestPointer(input.activePointer).length > 0;
  }

  private didMouseMove(clientX: number, clientY: number): boolean {
    const moved =
      this.lastMouseX === null ||
      this.lastMouseY === null ||
      Math.abs(clientX - this.lastMouseX) > this.mouseMoveThreshold ||
      Math.abs(clientY - this.lastMouseY) > this.mouseMoveThreshold;

    this.lastMouseX = clientX;
    this.lastMouseY = clientY;
    return moved;
  }

  private updateHoverState(isMouseOverPet: boolean): void {
    if (isMouseOverPet === this.isMouseOverPet) {
      return;
    }

    this.isMouseOverPet = isMouseOverPet;
    dispatchOverlayPetHoverState(isMouseOverPet);
  }
}

export default class DesktopPetOverlayScene extends Phaser.Scene {
  private spriteConfig: ISpriteConfig | null = null;
  private pet: OverlayPet | null = null;
  private readonly inputManager = new OverlayInputManager(this);
  private frameCount = 0;
  private isFlipped = false;
  private lastPetUiStateKey: string | null = null;
  private removeInteractionLockListener: (() => void) | null = null;

  private readonly forbiddenRandomStates = ["fall", "climb", "drag", "crawl", "jump"];
  private readonly frameRate = 9;
  private readonly updateDelay = 1000 / this.frameRate;
  private readonly petMoveVelocity = this.frameRate * 6;
  private readonly tweenAcceleration = this.frameRate * 1.1;
  private readonly randomStateDelay = 3000;
  private readonly flipDelay = 5000;
  private readonly allowPetClimbing = true;
  private readonly allowPetAboveTaskbar = false;

  constructor() {
    super({ key: "desktop-pet-overlay" });
  }

  preload(): void {
    this.spriteConfig = this.game.registry.get("spriteConfig") ?? null;
    if (!this.spriteConfig) {
      return;
    }

    this.load.spritesheet(this.spriteConfig.name, this.spriteConfig.imageSrc, {
      frameWidth: this.spriteConfig.frameSize,
      frameHeight: this.spriteConfig.frameSize,
    });
  }

  create(): void {
    this.cameras.main.setBackgroundColor("rgba(0, 0, 0, 0)");
    this.inputManager.turnOnIgnoreCursorEvents();
    this.physics.world.setBoundsCollision(true, true, true, true);
    this.updatePetAboveTaskbar();

    if (!this.spriteConfig) {
      return;
    }

    this.registerAnimations();
    this.addPet();
    this.bindOverlayInteractionLock();

    this.input.on("drag", (_pointer: Phaser.Input.Pointer, pet: OverlayPet, dragX: number, dragY: number) => {
      pet.x = dragX;
      pet.y = dragY;

      if (pet.anims && pet.anims.getName() !== this.stateKey("drag")) {
        this.switchState(pet, "drag");
      }

      if (pet.body?.enable) {
        pet.body.enable = false;
      }

      if (pet.x > (pet.input?.dragStartX ?? pet.x)) {
        if (this.isFlipped) {
          this.toggleFlipX(pet);
          this.isFlipped = false;
        }
      } else if (!this.isFlipped) {
        this.toggleFlipX(pet);
        this.isFlipped = true;
      }
    });

    this.input.on("dragend", (pointer: Phaser.Input.Pointer, pet: OverlayPet) => {
      this.tweens.add({
        targets: pet,
        x: pet.x + pointer.velocity.x * this.tweenAcceleration,
        y: pet.y + pointer.velocity.y * this.tweenAcceleration,
        duration: 600,
        ease: "Quart.easeOut",
        onComplete: () => {
          if (!pet.body?.enable) {
            pet.body.enable = true;
            window.setTimeout(() => {
              switch (pet.anims.getName()) {
                case this.stateKey("climb"):
                  this.updateDirection(pet, Direction.UP);
                  break;
                case this.stateKey("crawl"):
                  this.updateDirection(
                    pet,
                    pet.scaleX === -1 ? Direction.UPSIDELEFT : Direction.UPSIDERIGHT,
                  );
                  break;
              }
            }, 50);
          }
        },
      });

      this.petBeyondScreenSwitchClimb(pet, {
        up: this.getPetBoundTop(pet),
        down: this.getPetBoundDown(pet),
        left: this.getPetBoundLeft(pet),
        right: this.getPetBoundRight(pet),
      });
    });

    this.physics.world.on(
      "worldbounds",
      (body: Phaser.Physics.Arcade.Body, up: boolean, down: boolean, left: boolean, right: boolean) => {
        const pet = body.gameObject as OverlayPet;
        if (!pet?.anims) {
          return;
        }

        if (pet.anims.getName() === this.stateKey("crawl")) {
          if (left || right) {
            this.petJumpOrPlayRandomState(pet);
          }
          return;
        }

        if (up) {
          if (!this.allowPetClimbing) {
            this.petJumpOrPlayRandomState(pet);
            return;
          }

          if (pet.availableStates.includes("crawl")) {
            this.switchState(pet, "crawl");
            return;
          }

          this.petJumpOrPlayRandomState(pet);
        } else if (down) {
          this.switchStateAfterPetJump(pet);
          this.petOnTheGroundPlayRandomState(pet);
        }

        this.petBeyondScreenSwitchClimb(pet, { up, down, left, right });
      },
    );
  }

  update(_time: number, delta: number): void {
    this.emitPetUiState();
    this.frameCount += delta;
    if (this.frameCount < this.updateDelay) {
      return;
    }

    this.frameCount = 0;
    this.inputManager.checkIsMouseOnPet();
    if (this.pet) {
      this.petOnTheGroundPlayRandomState(this.pet);
    }
    this.randomJumpIfClimbing();
  }

  private registerAnimations(): void {
    if (!this.spriteConfig) {
      return;
    }

    for (const [stateName, range] of Object.entries(this.spriteConfig.states)) {
      const key = this.stateKey(stateName);
      if (this.anims.exists(key)) {
        continue;
      }

      this.anims.create({
        key,
        frames: this.anims.generateFrameNumbers(this.spriteConfig.name, {
          start: range.start,
          end: range.end,
        }),
        frameRate: this.spriteConfig.frameRates?.[stateName] ?? this.frameRate,
        repeat: -1,
      });
    }
  }

  private addPet(): void {
    if (!this.spriteConfig) {
      return;
    }

    const randomX = Phaser.Math.Between(100, Math.max(120, this.physics.world.bounds.width - 100));
    const petY = this.spriteConfig.frameSize;

    const pet = this.physics.add
      .sprite(randomX, petY, this.spriteConfig.name)
      .setInteractive({
        draggable: true,
        pixelPerfect: true,
      }) as OverlayPet;

    const scale = this.spriteConfig.scale ?? 1;
    pet.setScale(scale);
    pet.setCollideWorldBounds(true, 0, 0, true);
    if (pet.body) {
      pet.body.onWorldBounds = true;
    }
    pet.availableStates = Object.keys(this.spriteConfig.states);
    pet.canPlayRandomState = true;
    pet.canRandomFlip = true;
    pet.direction = Direction.UNKNOWN;
    pet.on("pointerdown", () => {
      dispatchOverlayPetActivate();
      dispatchOverlayInteractionLockState(true);
      this.inputManager.setInteractionLocked(true);
    });

    this.pet = pet;
    this.petJumpOrPlayRandomState(pet);
    this.emitPetUiState();
  }

  private stateKey(state: string): string {
    return `${state}-${this.spriteConfig?.name ?? "pet"}`;
  }

  private switchState(
    pet: OverlayPet,
    state: string,
    options: { repeat?: number; delay?: number; repeatDelay?: number } = {},
  ): void {
    if (!pet.anims || !pet.availableStates.includes(state)) {
      return;
    }

    if (!this.allowPetClimbing && (state === "climb" || state === "crawl")) {
      return;
    }

    const animationKey = this.stateKey(state);
    if (pet.anims.getName() === animationKey) {
      return;
    }

    pet.anims.play({
      key: animationKey,
      repeat: options.repeat ?? -1,
      delay: options.delay ?? 0,
      repeatDelay: options.repeatDelay ?? 0,
    });

    this.updateStateDirection(pet, state);
  }

  private updateStateDirection(pet: OverlayPet, state: string): void {
    let direction = Direction.UNKNOWN;

    switch (state) {
      case "walk":
        direction = pet.scaleX < 0 ? Direction.LEFT : Direction.RIGHT;
        break;
      case "jump":
        this.toggleFlipX(pet);
        direction = Direction.DOWN;
        break;
      case "climb":
        direction = Direction.UP;
        break;
      case "crawl":
        direction = pet.scaleX > 0 ? Direction.UPSIDELEFT : Direction.UPSIDERIGHT;
        break;
      default:
        direction = Direction.UNKNOWN;
        break;
    }

    this.updateDirection(pet, direction);
  }

  private updateDirection(pet: OverlayPet, direction: Direction): void {
    pet.direction = direction;
    this.updateMovement(pet);
  }

  private updateMovement(pet: OverlayPet): void {
    switch (pet.direction) {
      case Direction.RIGHT:
        pet.setVelocity(this.petMoveVelocity, 0);
        pet.setAcceleration(0);
        this.setPetLookToTheLeft(pet, false);
        break;
      case Direction.LEFT:
        pet.setVelocity(-this.petMoveVelocity, 0);
        pet.setAcceleration(0);
        this.setPetLookToTheLeft(pet, true);
        break;
      case Direction.UP:
        pet.setVelocity(0, -this.petMoveVelocity);
        pet.setAcceleration(0);
        break;
      case Direction.UPSIDELEFT:
        pet.setVelocity(-this.petMoveVelocity, 0);
        pet.setAcceleration(0);
        this.setPetLookToTheLeft(pet, true);
        break;
      case Direction.UPSIDERIGHT:
        pet.setVelocity(this.petMoveVelocity, -this.petMoveVelocity);
        pet.setAcceleration(0);
        this.setPetLookToTheLeft(pet, false);
        break;
      default:
        pet.setVelocity(0);
        pet.setAcceleration(0);
        break;
    }

    const isMovingUp = [Direction.UP, Direction.UPSIDELEFT, Direction.UPSIDERIGHT].includes(
      pet.direction ?? Direction.UNKNOWN,
    );
    pet.body?.setAllowGravity(!isMovingUp);

    if (pet.direction === Direction.UP) {
      pet.setVelocityX(0);
    }
  }

  private setPetLookToTheLeft(pet: OverlayPet, lookToTheLeft: boolean): void {
    if (lookToTheLeft) {
      if (pet.scaleX > 0) {
        this.toggleFlipX(pet);
      }
      return;
    }

    if (pet.scaleX < 0) {
      this.toggleFlipX(pet);
    }
  }

  private toggleFlipX(pet: OverlayPet): void {
    pet.scaleX > 0 ? pet.setOffset(pet.width, 0) : pet.setOffset(0, 0);
    pet.setScale(pet.scaleX * -1, pet.scaleY);
  }

  private toggleFlipXThenUpdateDirection(pet: OverlayPet): void {
    this.toggleFlipX(pet);
    switch (pet.direction) {
      case Direction.RIGHT:
        this.updateDirection(pet, Direction.LEFT);
        break;
      case Direction.LEFT:
        this.updateDirection(pet, Direction.RIGHT);
        break;
      case Direction.UPSIDELEFT:
        this.updateDirection(pet, Direction.UPSIDERIGHT);
        break;
      case Direction.UPSIDERIGHT:
        this.updateDirection(pet, Direction.UPSIDELEFT);
        break;
    }
  }

  private getOneRandomState(pet: OverlayPet): string {
    const allowed = pet.availableStates.filter((state) => !this.forbiddenRandomStates.includes(state));
    if (!allowed.length) {
      return pet.availableStates[0] ?? "stand";
    }
    return allowed[Phaser.Math.Between(0, allowed.length - 1)];
  }

  private playRandomState(pet: OverlayPet): void {
    if (!pet.canPlayRandomState) {
      return;
    }

    this.switchState(pet, this.getOneRandomState(pet));
    pet.canPlayRandomState = false;
    window.setTimeout(() => {
      pet.canPlayRandomState = true;
    }, this.randomStateDelay);
  }

  private switchStateAfterPetJump(pet: OverlayPet): void {
    if (!pet.anims || pet.anims.getName() !== this.stateKey("jump")) {
      return;
    }

    if (pet.availableStates.includes("fall")) {
      this.switchState(pet, "fall", { repeat: 0 });
      pet.canPlayRandomState = false;
      pet.once("animationcomplete", () => {
        pet.canPlayRandomState = true;
        this.playRandomState(pet);
      });
      return;
    }

    this.playRandomState(pet);
  }

  private petJumpOrPlayRandomState(pet: OverlayPet): void {
    if (pet.availableStates.includes("jump")) {
      this.switchState(pet, "jump");
      return;
    }

    this.switchState(pet, this.getOneRandomState(pet));
  }

  private petOnTheGroundPlayRandomState(pet: OverlayPet): void {
    if (!pet.anims) {
      return;
    }

    switch (pet.anims.getName()) {
      case this.stateKey("climb"):
      case this.stateKey("crawl"):
      case this.stateKey("drag"):
      case this.stateKey("jump"):
        return;
    }

    const random = Phaser.Math.Between(0, 2000);
    if (pet.anims.getName() === this.stateKey("walk")) {
      if (random >= 0 && random <= 5 && pet.availableStates.includes("sit")) {
        this.switchState(pet, "sit");
        window.setTimeout(() => {
          if (pet.anims && pet.anims.getName() === this.stateKey("sit")) {
            this.switchState(pet, pet.availableStates.includes("walk") ? "walk" : "stand");
          }
        }, Phaser.Math.Between(3000, 6000));
      }
      return;
    }

    if (random >= 888 && random <= 890) {
      if (pet.canRandomFlip) {
        this.toggleFlipXThenUpdateDirection(pet);
        pet.canRandomFlip = false;
        window.setTimeout(() => {
          pet.canRandomFlip = true;
        }, this.flipDelay);
      }
      return;
    }

    if (random >= 777 && random <= 780) {
      this.playRandomState(pet);
      return;
    }

    if (random >= 170 && random <= 175 && pet.availableStates.includes("walk")) {
      this.switchState(pet, "walk");
    }
  }

  private randomJumpIfClimbing(): void {
    const pet = this.pet;
    if (!pet?.anims) {
      return;
    }

    switch (pet.anims.getName()) {
      case this.stateKey("climb"):
      case this.stateKey("crawl"):
        break;
      default:
        return;
    }

    const random = Phaser.Math.Between(0, 500);
    if (random === 78) {
      let newPetX = pet.x;
      if (pet.anims.getName() === this.stateKey("climb")) {
        newPetX =
          pet.scaleX < 0
            ? Phaser.Math.Between(pet.x, Math.min(500, this.physics.world.bounds.width - 80))
            : Phaser.Math.Between(
                pet.x,
                Math.max(pet.x + 20, this.physics.world.bounds.width - 500),
              );
      }

      if (pet.body?.enable) {
        pet.body.enable = false;
      }
      this.switchState(pet, "jump");
      this.tweens.add({
        targets: pet,
        x: newPetX,
        y: this.getPetGroundPosition(pet),
        duration: 3000,
        ease: "Quad.easeOut",
        onComplete: () => {
          if (!pet.body?.enable) {
            pet.body.enable = true;
            this.switchStateAfterPetJump(pet);
          }
        },
      });
      return;
    }

    if (random >= 0 && random <= 5) {
      pet.anims.pause();
      this.updateDirection(pet, Direction.UNKNOWN);
      pet.body?.setAllowGravity(false);
      window.setTimeout(() => {
        if (pet.anims && !pet.anims.isPlaying) {
          pet.anims.resume();
          this.updateDirection(
            pet,
            pet.anims.getName() === this.stateKey("climb")
              ? Direction.UP
              : pet.scaleX < 0
                ? Direction.UPSIDELEFT
                : Direction.UPSIDERIGHT,
          );
        }
      }, Phaser.Math.Between(3000, 6000));
    }
  }

  private petBeyondScreenSwitchClimb(pet: OverlayPet, worldBounding: WorldBounding): void {
    if (!pet.anims) {
      return;
    }

    switch (pet.anims.getName()) {
      case this.stateKey("climb"):
      case this.stateKey("crawl"):
        return;
    }

    if (worldBounding.left || worldBounding.right) {
      if (pet.availableStates.includes("climb") && this.allowPetClimbing) {
        this.switchState(pet, "climb");
        const lastPetX = pet.x;
        if (worldBounding.left) {
          pet.setPosition(lastPetX - this.getPetLeftPosition(pet), pet.y);
          this.setPetLookToTheLeft(pet, true);
        } else {
          pet.setPosition(lastPetX + this.getPetRightPosition(pet), pet.y);
          this.setPetLookToTheLeft(pet, false);
        }
      }
    }
  }

  private getPetGroundPosition(pet: OverlayPet): number {
    return this.physics.world.bounds.height - pet.height * Math.abs(pet.scaleY) * pet.originY;
  }

  private getPetTopPosition(pet: OverlayPet): number {
    return pet.height * Math.abs(pet.scaleY) * pet.originY;
  }

  private getPetLeftPosition(pet: OverlayPet): number {
    return pet.width * Math.abs(pet.scaleX) * pet.originX;
  }

  private getPetRightPosition(pet: OverlayPet): number {
    return this.physics.world.bounds.width - pet.width * Math.abs(pet.scaleX) * pet.originX;
  }

  private getPetBoundDown(pet: OverlayPet): boolean {
    return pet.y >= this.getPetGroundPosition(pet);
  }

  private getPetBoundLeft(pet: OverlayPet): boolean {
    return pet.x <= this.getPetLeftPosition(pet);
  }

  private getPetBoundRight(pet: OverlayPet): boolean {
    return pet.y >= 0 && pet.x >= this.getPetRightPosition(pet);
  }

  private getPetBoundTop(pet: OverlayPet): boolean {
    return pet.y <= this.getPetTopPosition(pet);
  }

  private updatePetAboveTaskbar(): void {
    const boundsHeight = this.allowPetAboveTaskbar ? window.screen.height : window.screen.availHeight;
    this.physics.world.setBounds(0, 0, window.screen.width, boundsHeight);
  }

  private bindOverlayInteractionLock(): void {
    const handleInteractionLock = (event: Event) => {
      const locked = Boolean((event as CustomEvent<boolean>).detail);
      this.inputManager.setInteractionLocked(locked);
    };

    window.addEventListener(OVERLAY_INTERACTION_LOCK_EVENT, handleInteractionLock);
    this.removeInteractionLockListener = () => {
      window.removeEventListener(OVERLAY_INTERACTION_LOCK_EVENT, handleInteractionLock);
    };

    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      this.removeInteractionLockListener?.();
      this.removeInteractionLockListener = null;
      dispatchOverlayPetHoverState(false);
    });
  }

  private emitPetUiState(): void {
    const pet = this.pet;
    if (!pet?.active) {
      return;
    }

    const bounds = pet.getBounds();
    const detail = {
      anchorX: Math.round(bounds.centerX),
      petTop: Math.round(bounds.top),
      petBottom: Math.round(bounds.bottom),
      petLeft: Math.round(bounds.left),
      petRight: Math.round(bounds.right),
      petWidth: Math.round(bounds.width),
      petHeight: Math.round(bounds.height),
      facingLeft: pet.scaleX < 0,
    };

    const key = [
      detail.anchorX,
      detail.petTop,
      detail.petBottom,
      detail.petLeft,
      detail.petRight,
      detail.facingLeft ? 1 : 0,
    ].join(":");

    if (key === this.lastPetUiStateKey) {
      return;
    }

    this.lastPetUiStateKey = key;
    dispatchOverlayPetUiState(detail);
  }
}
