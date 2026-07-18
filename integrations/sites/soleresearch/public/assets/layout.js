(function exposeLayout(root, factory) {
  "use strict";
  const layout = factory();
  if (typeof module === "object" && module.exports) module.exports = layout;
  if (root) root.SoleResearchLayout = layout;
})(typeof globalThis === "object" ? globalThis : this, function createLayout() {
  "use strict";

  const EPSILON = 1e-7;

  function orderedNodes(nodes) {
    return nodes.slice().sort((left, right) => (
      Number(left.order || 0) - Number(right.order || 0)
      || String(left.id).localeCompare(String(right.id))
    ));
  }

  function displayedRadius(size, maximumScale = 1.2) {
    return Number(size) * Number(maximumScale) / 2;
  }

  function validateCircles(circles, {clearance = 0} = {}) {
    const collisions = [];
    for (let leftIndex = 0; leftIndex < circles.length; leftIndex += 1) {
      const left = circles[leftIndex];
      for (let rightIndex = leftIndex + 1; rightIndex < circles.length; rightIndex += 1) {
        const right = circles[rightIndex];
        const distance = Math.hypot(right.x - left.x, right.y - left.y);
        const required = left.radius + right.radius + clearance;
        if (distance + EPSILON < required) {
          collisions.push({left: left.id, right: right.id, distance, required});
        }
      }
    }
    return {valid: collisions.length === 0, collisions};
  }

  function boundsForCircles(circles, margin = 0) {
    if (!circles.length) return null;
    return {
      minX: Math.min(...circles.map((circle) => circle.x - circle.radius)) - margin,
      maxX: Math.max(...circles.map((circle) => circle.x + circle.radius)) + margin,
      minY: Math.min(...circles.map((circle) => circle.y - circle.radius)) - margin,
      maxY: Math.max(...circles.map((circle) => circle.y + circle.radius)) + margin,
    };
  }

  function boundsAreDisjoint(boundsList, clearance = 0) {
    for (let leftIndex = 0; leftIndex < boundsList.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < boundsList.length; rightIndex += 1) {
        const left = boundsList[leftIndex];
        const right = boundsList[rightIndex];
        const separated = left.maxX + clearance <= right.minX
          || right.maxX + clearance <= left.minX
          || left.maxY + clearance <= right.minY
          || right.maxY + clearance <= left.minY;
        if (!separated) return false;
      }
    }
    return true;
  }

  function graphIndex(nodes) {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const children = new Map();
    nodes.forEach((node) => {
      const parentId = byId.has(node.parentId) ? node.parentId : null;
      if (!children.has(parentId)) children.set(parentId, []);
      children.get(parentId).push(node);
    });
    children.forEach((items) => items.sort((left, right) => (
      Number(left.order || 0) - Number(right.order || 0)
      || String(left.id).localeCompare(String(right.id))
    )));
    return {byId, children};
  }

  function nodeDepths(nodes, byId) {
    const depths = new Map();
    nodes.forEach((node) => {
      let depth = 0;
      let cursor = node;
      const visited = new Set([node.id]);
      while (cursor.parentId && byId.has(cursor.parentId) && !visited.has(cursor.parentId)) {
        visited.add(cursor.parentId);
        cursor = byId.get(cursor.parentId);
        depth += 1;
      }
      depths.set(node.id, depth);
    });
    return depths;
  }

  function radialCircles(nodes, children, radii, depths, clearance, edgeClearance) {
    const circles = [];
    const visited = new Set();
    const extentCache = new Map();

    function subtreeExtent(node, ancestry = new Set()) {
      if (extentCache.has(node.id)) return extentCache.get(node.id);
      if (ancestry.has(node.id)) return radii.get(node.id);
      const nextAncestry = new Set(ancestry).add(node.id);
      const descendants = children.get(node.id) || [];
      const childExtent = descendants.length
        ? Math.max(...descendants.map((child) => subtreeExtent(child, nextAncestry))) + clearance
        : 0;
      const extent = radii.get(node.id) + childExtent;
      extentCache.set(node.id, extent);
      return extent;
    }

    function place(node, x, y, direction) {
      if (visited.has(node.id)) return;
      visited.add(node.id);
      const depth = depths.get(node.id) || 0;
      circles.push({id: node.id, x, y, radius: radii.get(node.id), depth});
      const descendants = (children.get(node.id) || []).filter((child) => !visited.has(child.id));
      if (!descendants.length) return;
      const maximumChildRadius = Math.max(...descendants.map((child) => subtreeExtent(child)));
      const span = depth === 0 ? Math.PI * 2 : Math.min(Math.PI * .92, .8 + descendants.length * .18);
      const angleStep = descendants.length > 1
        ? (depth === 0 ? Math.PI * 2 / descendants.length : span / (descendants.length - 1))
        : 0;
      const arcRadius = angleStep
        ? (maximumChildRadius * 2 + clearance) / (2 * Math.sin(angleStep / 2))
        : 0;
      const radius = Math.max(radii.get(node.id) + maximumChildRadius + edgeClearance, arcRadius);
      descendants.forEach((child, index) => {
        const angle = depth === 0
          ? -Math.PI / 2 + (Math.PI * 2 * index) / descendants.length
          : direction + (descendants.length === 1 ? 0 : (index / (descendants.length - 1) - .5) * span);
        place(child, x + Math.cos(angle) * radius, y + Math.sin(angle) * radius, angle);
      });
    }

    const roots = children.get(null) || [];
    roots.forEach((root, index) => place(root, index * 960, 0, -Math.PI / 2));
    orderedNodes(nodes).filter((node) => !visited.has(node.id)).forEach((node, index) => {
      place(node, (roots.length + index) * 960, 0, -Math.PI / 2);
    });
    return circles;
  }

  function spacedFallback(nodes, radii, depths, clearance, edgeClearance) {
    const {byId, children} = graphIndex(nodes);
    const xById = new Map();
    const visited = new Set();
    let leafCursor = 0;
    let previousLeafRadius = 0;
    function assignX(node) {
      if (visited.has(node.id)) return xById.get(node.id) || 0;
      visited.add(node.id);
      const descendants = (children.get(node.id) || []).filter((child) => !visited.has(child.id));
      if (descendants.length) {
        const childXs = descendants.map(assignX);
        const x = childXs.reduce((sum, value) => sum + value, 0) / childXs.length;
        xById.set(node.id, x);
        return x;
      }
      const radius = radii.get(node.id);
      if (xById.size) leafCursor += previousLeafRadius + radius + clearance + 8;
      xById.set(node.id, leafCursor);
      previousLeafRadius = radius;
      return leafCursor;
    }
    (children.get(null) || []).forEach(assignX);
    orderedNodes(nodes).filter((node) => !visited.has(node.id)).forEach(assignX);

    const layers = new Map();
    orderedNodes(nodes).forEach((node) => {
      const depth = depths.get(node.id) || 0;
      if (!layers.has(depth)) layers.set(depth, []);
      layers.get(depth).push(node);
    });
    let y = 0;
    let previousLayerRadius = 0;
    const yByDepth = new Map();
    Array.from(layers.entries()).sort((left, right) => left[0] - right[0]).forEach(([depth, layer], index) => {
      const layerRadius = Math.max(...layer.map((node) => radii.get(node.id)), 1);
      if (index) y += previousLayerRadius + layerRadius + edgeClearance + 8;
      yByDepth.set(depth, y);
      previousLayerRadius = layerRadius;
      const ordered = layer.slice().sort((left, right) => xById.get(left.id) - xById.get(right.id) || left.id.localeCompare(right.id));
      for (let itemIndex = 1; itemIndex < ordered.length; itemIndex += 1) {
        const previous = ordered[itemIndex - 1];
        const current = ordered[itemIndex];
        const minimumX = xById.get(previous.id) + radii.get(previous.id) + radii.get(current.id) + clearance + 8;
        if (xById.get(current.id) < minimumX) xById.set(current.id, minimumX);
      }
    });
    const centerX = Array.from(xById.values()).reduce((sum, value) => sum + value, 0) / Math.max(xById.size, 1);
    const centerY = Array.from(yByDepth.values()).reduce((sum, value) => sum + value, 0) / Math.max(yByDepth.size, 1);
    return orderedNodes(nodes).map((node) => ({
      id: node.id,
      x: xById.get(node.id) - centerX,
      y: yByDepth.get(depths.get(node.id) || 0) - centerY,
      radius: radii.get(node.id),
      depth: depths.get(node.id) || 0,
    }));
  }

  function fitScaleForBounds(bounds, width, height, {padding = 72, minimum = .01, maximum = 1.15} = {}) {
    if (!bounds) return 1;
    const contentWidth = Math.max(bounds.maxX - bounds.minX, 1);
    const contentHeight = Math.max(bounds.maxY - bounds.minY, 1);
    const availableWidth = Math.max(Number(width) - padding, 1);
    const availableHeight = Math.max(Number(height) - padding, 1);
    return Math.max(minimum, Math.min(maximum, availableWidth / contentWidth, availableHeight / contentHeight));
  }

  function fitScaleAroundPoint(bounds, point, width, height, {padding = 72, minimum = .01, maximum = 1.15} = {}) {
    if (!bounds || !point) return 1;
    const horizontalExtent = Math.max(point.x - bounds.minX, bounds.maxX - point.x, 1);
    const verticalExtent = Math.max(point.y - bounds.minY, bounds.maxY - point.y, 1);
    const halfWidth = Math.max((Number(width) - padding) / 2, 1);
    const halfHeight = Math.max((Number(height) - padding) / 2, 1);
    return Math.max(minimum, Math.min(maximum, halfWidth / horizontalExtent, halfHeight / verticalExtent));
  }

  function rootGuideLayout({width, height, count}) {
    const viewportWidth = Math.max(Number(width), 1);
    const viewportHeight = Math.max(Number(height), 1);
    const itemCount = Math.max(0, Number(count) || 0);
    const wide = viewportWidth >= 620;
    const compactItemHeight = 36;
    const compactGap = 4;
    const singleColumnHeight = itemCount * compactItemHeight + Math.max(0, itemCount - 1) * compactGap;
    const compactTwoColumn = !wide && viewportWidth >= 300 && singleColumnHeight > viewportHeight - 16;
    const columns = (wide || compactTwoColumn) && itemCount > 1 ? 2 : 1;
    const rows = Math.max(1, Math.ceil(itemCount / columns));
    const itemWidth = wide
      ? Math.min(160, Math.max(136, viewportWidth * .22))
      : compactTwoColumn
        ? (viewportWidth - 40) / 2
        : Math.min(220, Math.max(1, viewportWidth - 16));
    const itemHeight = wide ? 46 : compactItemHeight;
    const gap = wide ? 8 : compactGap;
    const totalHeight = rows * itemHeight + Math.max(0, rows - 1) * gap;
    const startY = Math.max(8, (viewportHeight - totalHeight) / 2);
    const xPositions = columns === 2 ? [12, viewportWidth - 12 - itemWidth] : [viewportWidth - 8 - itemWidth];
    const rects = Array.from({length: itemCount}, (_unused, index) => {
      const column = columns === 2 ? Math.floor(index / rows) : 0;
      const row = columns === 2 ? index % rows : index;
      return {
        index,
        x: xPositions[column],
        y: startY + row * (itemHeight + gap),
        width: itemWidth,
        height: itemHeight,
      };
    });
    return {columns, rows, rects};
  }

  function layoutForest(nodes, {clearance = 16, maximumScale = 1.2} = {}) {
    const normalized = orderedNodes(nodes).map((node) => ({
      id: String(node.id),
      parentId: node.parentId === null || node.parentId === undefined ? null : String(node.parentId),
      order: Number(node.order || 0),
      size: Number(node.size),
    }));
    if (!normalized.length) return {mode: "empty", circles: [], bounds: null};
    const {byId, children} = graphIndex(normalized);
    const depths = nodeDepths(normalized, byId);
    const radii = new Map(normalized.map((node) => [node.id, displayedRadius(node.size, maximumScale)]));
    // Parent-child gaps must also hold the 36px path target and its 10px safety margin.
    const edgeClearance = Math.max(clearance, 56);
    const preferred = radialCircles(normalized, children, radii, depths, clearance, edgeClearance);
    let preferredAudit = validateCircles(preferred, {clearance});
    let expandedPreferred = preferred;
    let expansionFactor = 1;
    for (let attempt = 1; attempt <= 12 && !preferredAudit.valid; attempt += 1) {
      expansionFactor = 1 + attempt * .35;
      expandedPreferred = preferred.map((circle) => ({
        ...circle,
        x: circle.x * expansionFactor,
        y: circle.y * expansionFactor,
      }));
      preferredAudit = validateCircles(expandedPreferred, {clearance});
    }
    const circles = preferredAudit.valid ? expandedPreferred : spacedFallback(normalized, radii, depths, clearance, edgeClearance);
    const finalAudit = validateCircles(circles, {clearance});
    if (!finalAudit.valid) throw new Error("deterministic layout fallback produced intersecting circles");
    return {
      mode: preferredAudit.valid ? (expansionFactor === 1 ? "radial" : "radial-expanded") : "spaced",
      circles,
      bounds: boundsForCircles(circles, clearance),
    };
  }

  function placeSatellites({parent, count, radius, occupied, clearance = 16, preferredAngle = 0}) {
    const placed = [];
    const allOccupied = occupied.slice();
    const angleSteps = 72;
    for (let index = 0; index < count; index += 1) {
      let selected = null;
      for (let ring = 0; ring < 32 && !selected; ring += 1) {
        const distance = parent.radius + radius + clearance + ring * (radius * 2 + clearance);
        for (let angleIndex = 0; angleIndex < angleSteps; angleIndex += 1) {
          const angle = preferredAngle + (Math.PI * 2 * (angleIndex + index * 11)) / angleSteps;
          const candidate = {
            id: `${parent.id}:evidence:${index}`,
            x: parent.x + Math.cos(angle) * distance,
            y: parent.y + Math.sin(angle) * distance,
            radius,
            depth: Number(parent.depth || 0) + 1,
          };
          if (validateCircles([...allOccupied, candidate], {clearance}).valid) {
            selected = candidate;
            break;
          }
        }
      }
      if (!selected) throw new Error(`unable to place evidence circle ${index}`);
      placed.push(selected);
      allOccupied.push(selected);
    }
    return placed;
  }

  function routeCue(node) {
    const title = String(node && node.title || "")
      .replace(/^\d+(?:\.\d+)*[.)]?\s+/, "")
      .replace(/[`*_#:[\]()]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    const normalized = title.toLocaleLowerCase();
    const topicFamilies = [
      [/\bimage filtering\b/, "Smooth, denoise, and sharpen"],
      [/\bmorpholog(?:y|ical)(?: operations?)?\b/, "Clean and reshape regions"],
      [/\bgeometric transformations?\b/, "Align, warp, and measure"],
      [/\bedge\s*(?:&|and)\s*region detection\b/, "Find boundaries and segments"],
      [/\bfeature detection\s*(?:&|and)\s*matching\b/, "Recognize and match landmarks"],
      [/\bfrequency domain(?: methods?)?\b/, "Analyze patterns by frequency"],
      [/\bshape\s*(?:&|and)\s*geometry analysis\b/, "Measure contours and geometry"],
      [/\bcolor\s*(?:&|and)\s*intensity processing\b/, "Adjust contrast and color"],
    ];
    const topicFamily = topicFamilies.find(([pattern]) => pattern.test(normalized));
    if (topicFamily) return topicFamily[1];
    const translations = [
      [/\b(history|historical|established)\b/, "How the field evolved"],
      [/\b(method|methods|methodology|mechanism|process|pipeline|workflow|architecture)\b/, "How it works"],
      [/\b(application|applications|use case|use cases|when)\b/, "When to use it"],
      [/\b(limit|limits|limitation|limitations|tradeoff|tradeoffs|weakness|risk)\b/, "Tradeoffs and limits"],
      [/\b(compare|comparison|benchmark|alternatives|versus)\b/, "How options compare"],
      [/\b(evidence|result|results|finding|findings)\b/, "What the evidence shows"],
    ];
    const translated = translations.find(([pattern]) => pattern.test(normalized));
    if (translated) return translated[1];
    const source = title || String(node && node.body || "").replace(/\s+/g, " ").trim();
    const words = source.split(/\s+/).filter(Boolean).slice(0, 7);
    if (words.length >= 3) return words.join(" ");
    return ["Explore", ...words, "topic", "details"].slice(0, 3).join(" ");
  }

  function routeCueWidth(cue) {
    return Math.min(160, Math.max(88, 28 + String(cue || "").length * 6.2));
  }

  function routeFootprintRadius(cue, targetRadius) {
    const estimatedWidth = routeCueWidth(cue);
    return Math.max(targetRadius, Math.hypot(estimatedWidth, 28) / 2);
  }

  function routeFootprintCircles(routes) {
    return routes.filter((route) => (
      route.persistent
      && Number.isFinite(route.cueX)
      && Number.isFinite(route.cueY)
    )).map((route) => ({
      id: `route:${route.id}`,
      x: route.cueX,
      y: route.cueY,
      radius: route.footprintRadius,
      depth: route.depth,
    }));
  }

  function routeTargetCircles(routes) {
    return routes.map((route) => ({
      id: `route-target:${route.id}`,
      x: route.x,
      y: route.y,
      radius: route.targetRadius,
      depth: route.depth,
    }));
  }

  function placeRoutes({edges, nodes, targetRadius = 18, clearance = 10}) {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const placed = [];
    const routeTargets = [];
    const orderedEdges = edges.slice().sort((left, right) => String(left.id).localeCompare(String(right.id)));
    orderedEdges.forEach((edge) => {
      const source = byId.get(edge.sourceId);
      const destination = byId.get(edge.destinationId);
      if (!source || !destination) throw new Error(`route ${edge.id} has an unavailable endpoint`);
      const dx = destination.x - source.x;
      const dy = destination.y - source.y;
      const length = Math.hypot(dx, dy);
      if (!length) throw new Error(`route ${edge.id} has coincident endpoints`);
      const tangentX = -dy / length;
      const tangentY = dx / length;
      const footprintRadius = routeFootprintRadius(edge.cue, targetRadius);
      const cueWidth = routeCueWidth(edge.cue);
      const sourceTangent = Math.min(.86, Math.max(.14, (source.radius + targetRadius + clearance) / length));
      const destinationTangent = Math.min(.86, Math.max(.14, 1 - (destination.radius + targetRadius + clearance) / length));
      const preferredFractions = edge.persistent
        ? [.5, .62, .38, .72, .28, destinationTangent, sourceTangent]
        : [sourceTangent, destinationTangent, (sourceTangent + destinationTangent) / 2, .5, .62, .38, .72, .28];
      const fractions = Array.from(new Set(preferredFractions.map((value) => Math.round(value * 1000000) / 1000000)));
      let selected = null;
      const offsetStep = 12;
      function candidateAt(x, y, projection, perpendicular) {
        return {
          id: String(edge.id),
          sourceId: edge.sourceId,
          destinationId: edge.destinationId,
          x,
          y,
          targetRadius,
          footprintRadius,
          cueWidth,
          persistent: Boolean(edge.persistent),
          angle: Math.atan2(dy, dx),
          projection,
          perpendicular,
          depth: Math.max(Number(source.depth || 0), Number(destination.depth || 0)),
        };
      }
      function targetIsSafe(candidate) {
        const candidateTarget = {
          id: `route-target:${candidate.id}`,
          x: candidate.x,
          y: candidate.y,
          radius: targetRadius,
          depth: candidate.depth,
        };
        const nodeSafe = validateCircles([...nodes, candidateTarget], {clearance}).valid;
        const targetSafe = validateCircles([...routeTargets, candidateTarget], {clearance}).valid;
        return nodeSafe && targetSafe;
      }
      for (let offsetIndex = 0; offsetIndex <= 4 && !selected; offsetIndex += 1) {
        const signs = offsetIndex === 0 ? [0] : [1, -1];
        for (const sign of signs) {
          for (const fraction of fractions) {
            const candidate = candidateAt(
              source.x + dx * fraction + tangentX * offsetStep * offsetIndex * sign,
              source.y + dy * fraction + tangentY * offsetStep * offsetIndex * sign,
              fraction,
              offsetStep * offsetIndex * sign
            );
            if (targetIsSafe(candidate)) {
              selected = candidate;
              break;
            }
          }
          if (selected) break;
        }
      }
      if (!selected) throw new Error(`unable to place route ${edge.id} without an intersection`);
      placed.push(selected);
      routeTargets.push({
        id: `route-target:${selected.id}`,
        x: selected.x,
        y: selected.y,
        radius: selected.targetRadius,
        depth: selected.depth,
      });
    });

    const persistentFootprints = [];
    const cuePlacementOrder = placed.filter((route) => route.persistent);
    cuePlacementOrder.forEach((route) => {
      const source = byId.get(route.sourceId);
      const destination = byId.get(route.destinationId);
      const dx = destination.x - source.x;
      const dy = destination.y - source.y;
      const length = Math.hypot(dx, dy);
      const tangentX = -dy / length;
      const tangentY = dx / length;
      const sourceFraction = Math.min(.82, Math.max(.18, (source.radius + route.footprintRadius + clearance) / length));
      const destinationFraction = Math.min(.82, Math.max(.18, 1 - (destination.radius + route.footprintRadius + clearance) / length));
      const preferredFractions = [.5, .38, .62, .28, .72, sourceFraction, destinationFraction];
      const fractions = Array.from(new Set(preferredFractions.map((value) => Math.round(value * 1000000) / 1000000)));
      let cue = null;
      for (let offsetIndex = 0; offsetIndex <= 18 && !cue; offsetIndex += 1) {
        const signs = offsetIndex === 0 ? [0] : [1, -1];
        for (const sign of signs) {
          for (const fraction of fractions) {
            const perpendicular = 12 * offsetIndex * sign;
            const candidateCue = {
              id: `route:${route.id}`,
              x: source.x + dx * fraction + tangentX * perpendicular,
              y: source.y + dy * fraction + tangentY * perpendicular,
              radius: route.footprintRadius,
              depth: route.depth,
              projection: fraction,
              perpendicular,
            };
            const clearOfScene = validateCircles([...nodes, ...routeTargets, ...persistentFootprints, candidateCue], {clearance}).valid;
            if (clearOfScene) {
              cue = candidateCue;
              break;
            }
          }
          if (cue) break;
        }
      }
      if (!cue) throw new Error(`unable to place route cue ${route.id} without an intersection`);
      route.cueX = cue.x;
      route.cueY = cue.y;
      route.cueProjection = cue.projection;
      route.cuePerpendicular = cue.perpendicular;
      if (route.persistent) persistentFootprints.push(cue);
    });
    return placed;
  }

  function routeNavigationState({destinationId, hasChildren, expandedIds}) {
    const expanded = new Set(expandedIds || []);
    if (hasChildren) expanded.add(destinationId);
    return {
      selectedNodeId: destinationId,
      focusRootId: destinationId,
      draftNodeId: destinationId,
      inspectNodeId: destinationId,
      urlTopic: destinationId,
      pushHistory: true,
      expandedIds: Array.from(expanded).sort(),
    };
  }

  function isRouteActivation({type, key = ""}) {
    return type === "click" || (type === "keydown" && (key === "Enter" || key === " "));
  }

  return {
    boundsForCircles,
    boundsAreDisjoint,
    displayedRadius,
    fitScaleAroundPoint,
    fitScaleForBounds,
    isRouteActivation,
    layoutForest,
    placeRoutes,
    placeSatellites,
    routeCue,
    routeFootprintCircles,
    routeTargetCircles,
    routeNavigationState,
    rootGuideLayout,
    validateCircles,
  };
});
