.pragma library

// =============================================================================
// DIAGRAM FUNCTIONS
//
// Generic Canvas drawing utilities shared by the Shuttle and Sample diagram
// components on the Milling Angle Calc page.
//
// This library contains NO instrument geometry. The SEM/FIB/GIS angles and
// the stage tilt conventions live in millingAngleCalculations.js; callers
// pass those angles in. For example, the FIB reference line is drawn with:
//
//     Draw.drawRadialLine(ctx, cx, cy, r, len,
//                         MillingAngleCalculations.fibScreenAngleDeg(),
//                         strokeStyle, lineWidth)
//
// Angle convention (matches millingAngleCalculations.js screen angles):
//   0 deg = horizontal right, positive = counter-clockwise.
//   Conversion to the canvas' clockwise-positive y-down frame is handled
//   internally by each function.
// =============================================================================

// -----------------------------------------------------------------------------
// CORE DRAWING
// -----------------------------------------------------------------------------

// Generic line drawing function (used internally by other draw functions).
function drawLine(ctx, startX, startY, endX, endY, strokeStyle, lineWidth) {
    ctx.strokeStyle = strokeStyle;
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.lineTo(endX, endY);
    ctx.stroke();
}

// -----------------------------------------------------------------------------
// RADIAL LINES - Lines at arbitrary screen angles from the figure center
// -----------------------------------------------------------------------------

// Draw a radial line starting at the reference circle edge, extending
// outward by lineLength at the given screen angle.
function drawRadialLine(ctx, centerX, centerY, referenceCircleRadius,
                        lineLength, angleDeg, strokeStyle, lineWidth) {
    var angleRad = angleDeg * Math.PI / 180;
    var startX = centerX + referenceCircleRadius * Math.cos(angleRad);
    var startY = centerY - referenceCircleRadius * Math.sin(angleRad);
    var endX = centerX + (referenceCircleRadius + lineLength) * Math.cos(angleRad);
    var endY = centerY - (referenceCircleRadius + lineLength) * Math.sin(angleRad);
    drawLine(ctx, startX, startY, endX, endY, strokeStyle, lineWidth);
}

// Draw a diameter line through the center at a specified screen angle.
function drawDiameterLine(ctx, centerX, centerY, referenceCircleRadius,
                          angleDeg, strokeStyle, lineWidth) {
    var angleRad = angleDeg * Math.PI / 180;
    var startX = centerX - referenceCircleRadius * Math.cos(angleRad);
    var startY = centerY + referenceCircleRadius * Math.sin(angleRad);
    var endX = centerX + referenceCircleRadius * Math.cos(angleRad);
    var endY = centerY - referenceCircleRadius * Math.sin(angleRad);
    drawLine(ctx, startX, startY, endX, endY, strokeStyle, lineWidth);
}

// Draw an asymmetric line from the center: extends lineLength in the
// positive angle direction, and only to the reference circle edge in the
// opposite direction.
function drawAsymmetricCenterLine(ctx, centerX, centerY, referenceCircleRadius,
                                  lineLength, angleDeg, strokeStyle, lineWidth) {
    var angleRad = angleDeg * Math.PI / 180;
    var startX = centerX - referenceCircleRadius * Math.cos(angleRad);
    var startY = centerY + referenceCircleRadius * Math.sin(angleRad);
    var endX = centerX + lineLength * Math.cos(angleRad);
    var endY = centerY - lineLength * Math.sin(angleRad);
    drawLine(ctx, startX, startY, endX, endY, strokeStyle, lineWidth);
}

// Draw the left and right horizontal reference lines.
function drawHorizontalLines(ctx, centerX, centerY, referenceCircleRadius,
                             lineLength, strokeStyle, lineWidth) {
    drawRadialLine(ctx, centerX, centerY, referenceCircleRadius,
                   lineLength, 0, strokeStyle, lineWidth);
    drawRadialLine(ctx, centerX, centerY, referenceCircleRadius,
                   lineLength, 180, strokeStyle, lineWidth);
}

// -----------------------------------------------------------------------------
// ARC DRAWING
// -----------------------------------------------------------------------------

// Draw an arc between two screen angles (counter-clockwise from startAngleDeg
// to endAngleDeg when endAngleDeg > startAngleDeg).
function drawArc(ctx, centerX, centerY, radius, startAngleDeg,
                 endAngleDeg, strokeStyle, lineWidth) {
    ctx.strokeStyle = strokeStyle;
    ctx.lineWidth = lineWidth;
    // Negate angles to convert from CCW screen convention to canvas CW.
    var startAngle = -startAngleDeg * Math.PI / 180;
    var endAngle = -endAngleDeg * Math.PI / 180;
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, startAngle, endAngle, true);
    ctx.stroke();
}

// -----------------------------------------------------------------------------
// LABEL POSITIONING HELPERS
// -----------------------------------------------------------------------------

// Calculate label position at the end of a radial line, optionally offset
// further outward by labelOffset. Returns {x, y}.
function getRadialLabelPosition(centerX, centerY, referenceCircleRadius,
                                lineLength, angleDeg, labelOffset) {
    var angleRad = angleDeg * Math.PI / 180;
    var offset = labelOffset || 0;
    var distance = referenceCircleRadius + lineLength + offset;
    return {
        x: centerX + distance * Math.cos(angleRad),
        y: centerY - distance * Math.sin(angleRad)
    };
}

// -----------------------------------------------------------------------------
// INTERSECTION CALCULATIONS - For the chalk line feature
// -----------------------------------------------------------------------------

// Calculate intersections between a ray (from a point at a screen angle)
// and a rotated rectangle. rectRotationDeg uses the QML Item.rotation
// convention (clockwise-positive). Returns an array of points [{x, y}, ...].
function lineRotatedRectangleIntersection(lineCenterX, lineCenterY, lineAngleDeg,
                                          rectCenterX, rectCenterY,
                                          rectWidth, rectHeight, rectRotationDeg) {
    var intersections = [];
    var rectRotRad = rectRotationDeg * Math.PI / 180;  // CW-positive in y-down frame

    // Rectangle corners in local coordinates (before rotation).
    var corners = [
        {x: -rectWidth / 2, y: -rectHeight / 2},   // top-left
        {x: rectWidth / 2, y: -rectHeight / 2},    // top-right
        {x: rectWidth / 2, y: rectHeight / 2},     // bottom-right
        {x: -rectWidth / 2, y: rectHeight / 2}     // bottom-left
    ];

    // Transform corners to global (canvas) coordinates.
    var rotatedCorners = corners.map(function(corner) {
        return {
            x: rectCenterX + corner.x * Math.cos(rectRotRad) - corner.y * Math.sin(rectRotRad),
            y: rectCenterY + corner.x * Math.sin(rectRotRad) + corner.y * Math.cos(rectRotRad)
        };
    });

    // Check intersection with each edge.
    for (var i = 0; i < 4; i++) {
        var p1 = rotatedCorners[i];
        var p2 = rotatedCorners[(i + 1) % 4];

        var intersection = lineLineIntersection(
            lineCenterX, lineCenterY, lineAngleDeg,
            p1.x, p1.y, p2.x, p2.y
        );

        if (intersection) {
            // Avoid duplicate points at corners.
            var isDuplicate = intersections.some(function(existing) {
                var dx = existing.x - intersection.x;
                var dy = existing.y - intersection.y;
                return Math.sqrt(dx * dx + dy * dy) < 0.1;
            });
            if (!isDuplicate) {
                intersections.push(intersection);
            }
        }
    }

    return intersections;
}

// Calculate the intersection between a ray (from a point at a screen angle,
// extending in BOTH directions, i.e. a full line) and a line segment.
// Returns {x, y} or null if no intersection on the segment.
function lineLineIntersection(rayCenterX, rayCenterY, rayAngleDeg,
                              segX1, segY1, segX2, segY2) {
    var rayAngleRad = rayAngleDeg * Math.PI / 180;

    // Ray direction vector (negative Y: canvas y axis points down).
    var rayDx = Math.cos(rayAngleRad);
    var rayDy = -Math.sin(rayAngleRad);

    // Segment direction vector.
    var segDx = segX2 - segX1;
    var segDy = segY2 - segY1;

    // Solve using parametric equations.
    var denominator = rayDx * segDy - rayDy * segDx;
    if (Math.abs(denominator) < 0.0001) {
        return null;  // Parallel or collinear.
    }

    var t = ((segX1 - rayCenterX) * segDy - (segY1 - rayCenterY) * segDx) / denominator;
    var u = ((segX1 - rayCenterX) * rayDy - (segY1 - rayCenterY) * rayDx) / denominator;

    // Check if the intersection lies on the segment (0 <= u <= 1).
    if (u >= 0 && u <= 1) {
        return {
            x: rayCenterX + t * rayDx,
            y: rayCenterY + t * rayDy
        };
    }

    return null;
}
