import * as Cesium from "cesium";
import { LINK_GRADES, gradeLink, shortId, type LinkGrade } from "../taxonomy";
import type { RuntimeSnapshot } from "../types";

/**
 * Peer-link rendering.
 *
 * Link geometry follows the interpolated aircraft positions, so a relay flying
 * into position visibly drags its links with it and the operator watches
 * connectivity rebuild rather than seeing it snap between snapshots.
 */

export type NetworkOptions = {
  showLinks: boolean;
  showDownLinks: boolean;
  showLinkLabels: boolean;
  showRelayRange: boolean;
};

type LinkVisual = {
  id: string;
  source: string;
  target: string;
  grade: LinkGrade;
  quality: number;
  distance: number;
  obstructed: boolean;
  positions: Cesium.Cartesian3[];
  midpoint: Cesium.Cartesian3;
  label: string;
  color: Cesium.Color;
  width: number;
  dash: number;
  visible: boolean;
  emphasised: boolean;
};

type RelayVisual = {
  id: string;
  position: Cesium.Cartesian3;
  radius: number;
  visible: boolean;
};

export class NetworkLayer {
  private readonly links = new Map<string, LinkVisual>();
  private readonly linkEntities = new Map<string, Cesium.Entity[]>();
  private readonly relays = new Map<string, RelayVisual>();
  private readonly relayEntities = new Map<string, Cesium.Entity>();
  private options: NetworkOptions = {
    showLinks: true,
    showDownLinks: true,
    showLinkLabels: false,
    showRelayRange: true,
  };
  private focusId: string | null = null;

  constructor(
    private readonly viewer: Cesium.Viewer,
    private readonly positionOf: (nodeId: string) => Cesium.Cartesian3 | undefined,
  ) {}

  setOptions(options: Partial<NetworkOptions>): void {
    this.options = { ...this.options, ...options };
  }

  setFocus(nodeId: string | null): void {
    this.focusId = nodeId;
  }

  update(snapshot: RuntimeSnapshot): void {
    const seen = new Set<string>();
    const relayRange = new Map<string, number>();

    for (const link of snapshot.links) {
      const id = `${link.source_id}|${link.target_id}`;
      seen.add(id);
      const source = this.positionOf(link.source_id);
      const target = this.positionOf(link.target_id);
      let visual = this.links.get(id);
      if (!visual) {
        visual = {
          id,
          source: link.source_id,
          target: link.target_id,
          grade: "DOWN",
          quality: 0,
          distance: 0,
          obstructed: false,
          positions: [new Cesium.Cartesian3(), new Cesium.Cartesian3()],
          midpoint: new Cesium.Cartesian3(),
          label: "",
          color: Cesium.Color.WHITE.clone(),
          width: 2,
          dash: 0xffff,
          visible: false,
          emphasised: false,
        };
        this.links.set(id, visual);
        this.linkEntities.set(id, this.createLinkEntities(visual));
      }

      const grade = gradeLink(link.available, link.quality);
      const style = LINK_GRADES[grade];
      visual.grade = grade;
      visual.quality = link.quality;
      visual.distance = link.distance_m;
      visual.obstructed = link.obstructed;
      visual.color = Cesium.Color.fromCssColorString(style.color);
      visual.width = style.width;
      visual.dash = style.dash;
      visual.emphasised =
        this.focusId !== null && (link.source_id === this.focusId || link.target_id === this.focusId);
      visual.label = `${shortId(link.source_id)}–${shortId(link.target_id)}  ${Math.round(link.quality * 100)}%${link.obstructed ? "  LOS BLOCKED" : ""}`;

      const hasEndpoints = Boolean(source && target);
      if (source && target) {
        Cesium.Cartesian3.clone(source, visual.positions[0]);
        Cesium.Cartesian3.clone(target, visual.positions[1]);
        Cesium.Cartesian3.midpoint(source, target, visual.midpoint);
      }
      visual.visible =
        hasEndpoints && this.options.showLinks && (link.available || this.options.showDownLinks);

      if (link.available) {
        relayRange.set(link.source_id, Math.max(relayRange.get(link.source_id) ?? 0, link.distance_m));
        relayRange.set(link.target_id, Math.max(relayRange.get(link.target_id) ?? 0, link.distance_m));
      }
    }

    for (const [id, entities] of this.linkEntities) {
      if (seen.has(id)) continue;
      for (const entity of entities) this.viewer.entities.remove(entity);
      this.linkEntities.delete(id);
      this.links.delete(id);
    }

    this.updateRelays(snapshot, relayRange);
  }

  /**
   * Relay ring radius is the longest link that node is actually holding, so the
   * ring shows demonstrated reach instead of a guessed radio range.
   */
  private updateRelays(snapshot: RuntimeSnapshot, relayRange: Map<string, number>): void {
    const active = new Set(snapshot.network.relay_nodes);
    for (const nodeId of active) {
      const position = this.positionOf(nodeId);
      let visual = this.relays.get(nodeId);
      if (!visual) {
        visual = { id: nodeId, position: new Cesium.Cartesian3(), radius: 40, visible: false };
        this.relays.set(nodeId, visual);
        this.relayEntities.set(nodeId, this.createRelayEntity(visual));
      }
      if (position) Cesium.Cartesian3.clone(position, visual.position);
      visual.radius = Math.max(35, relayRange.get(nodeId) ?? 35);
      visual.visible = Boolean(position) && this.options.showRelayRange;
    }
    for (const [nodeId, visual] of this.relays) {
      if (!active.has(nodeId)) visual.visible = false;
    }
  }

  private createLinkEntities(visual: LinkVisual): Cesium.Entity[] {
    const line = this.viewer.entities.add({
      id: `runtime-link-${visual.id}`,
      polyline: {
        positions: new Cesium.CallbackProperty(() => visual.positions, false),
        width: new Cesium.CallbackProperty(
          () => visual.width * (visual.emphasised ? 1.7 : 1),
          false,
        ),
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({
          color: new Cesium.CallbackProperty(() => {
            const base = visual.grade === "DOWN" ? 0.3 : 0.45 + 0.5 * visual.quality;
            return visual.color.withAlpha(Math.min(1, base * (visual.emphasised ? 1.4 : 1)));
          }, false),
          dashPattern: new Cesium.CallbackProperty(() => visual.dash, false),
          dashLength: 18,
        }),
        show: new Cesium.CallbackProperty(() => visual.visible, false),
      },
    });

    const label = this.viewer.entities.add({
      id: `runtime-linklabel-${visual.id}`,
      position: new Cesium.CallbackPositionProperty(() => visual.midpoint, false),
      label: {
        text: new Cesium.CallbackProperty(() => visual.label, false),
        font: "600 11px ui-monospace, SFMono-Regular, Menlo, monospace",
        fillColor: new Cesium.CallbackProperty(() => visual.color, false),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#070c11").withAlpha(0.8),
        backgroundPadding: new Cesium.Cartesian2(7, 4),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        translucencyByDistance: new Cesium.NearFarScalar(1500, 1, 7000, 0),
        show: new Cesium.CallbackProperty(
          () => visual.visible && (this.options.showLinkLabels || visual.emphasised),
          false,
        ),
      },
    });

    return [line, label];
  }

  private createRelayEntity(visual: RelayVisual): Cesium.Entity {
    return this.viewer.entities.add({
      id: `runtime-relayring-${visual.id}`,
      position: new Cesium.CallbackPositionProperty(() => visual.position, false),
      ellipse: {
        semiMajorAxis: new Cesium.CallbackProperty(() => visual.radius, false),
        semiMinorAxis: new Cesium.CallbackProperty(() => visual.radius, false),
        height: 1,
        material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString("#b78bff").withAlpha(0.07)),
        outline: true,
        outlineWidth: 2,
        outlineColor: Cesium.Color.fromCssColorString("#b78bff").withAlpha(0.55),
        show: new Cesium.CallbackProperty(() => visual.visible, false),
      },
    });
  }

  destroy(): void {
    for (const entities of this.linkEntities.values()) {
      for (const entity of entities) this.viewer.entities.remove(entity);
    }
    for (const entity of this.relayEntities.values()) this.viewer.entities.remove(entity);
    this.linkEntities.clear();
    this.relayEntities.clear();
    this.links.clear();
    this.relays.clear();
  }
}
