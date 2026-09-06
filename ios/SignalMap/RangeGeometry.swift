import Foundation

struct GeometrySolution {
    var positions: [String: Vector3]
    var rms: Double
    var heightResolved: Bool
}

enum RangeGeometry {
    /// Complete, contemporaneous distance graphs only. Missing edges are never filled with zero.
    /// Axes are a group convention, not device heading or gravity.
    static func solve(ids: [String], distances: [RangingPair: Double], previous: [String: Vector3] = [:], flat: Bool = false, line: Bool = false) -> GeometrySolution? {
        let ids = Array(Set(ids)).sorted(), n = Set(ids).count
        if line {
            // One range constrains distance only. The north/south axis is explicitly assumed.
            guard n == 2, let d = distances[.init(ids[0],ids[1])], d.isFinite, d > 0.05, d < 1000 else { return nil }
            return .init(positions:[ids[0]:.init(x:0,y:-d/2,z:0),ids[1]:.init(x:0,y:d/2,z:0)],rms:0,heightResolved:false)
        }
        guard n >= (flat ? 3 : 4) && n <= 5 else { return nil }
        var squared = Array(repeating: Array(repeating: 0.0, count: n), count: n)
        for i in 0..<n {
            for j in (i+1)..<n {
                guard let distance = distances[.init(ids[i], ids[j])], distance.isFinite,
                      distance > 0.05, distance < 1000 else { return nil }
                squared[i][j] = distance*distance; squared[j][i] = distance*distance
            }
        }
        let means = squared.map { $0.reduce(0,+) / Double(n) }
        let mean = means.reduce(0,+) / Double(n)
        var gram = Array(repeating: Array(repeating: 0.0, count: n), count: n)
        for i in 0..<n { for j in 0..<n {
            let centered: Double = squared[i][j] - means[i] - means[j] + mean
            gram[i][j] = -0.5 * centered
        } }
        let (values, vectors) = eigen(gram)
        let order = values.indices.sorted { values[$0] > values[$1] }
        guard values[order[0]] > 0.01, flat || values[order[1]] > 0.01 else { return nil }
        // Large negative eigenvalues indicate mutually inconsistent/noisy distances.
        guard (values.min() ?? 0) >= -max(0.15, values[order[0]] * 0.08) else { return nil }
        var points = (0..<n).map { i -> Vector3 in
            let c = (0..<3).map { k in vectors[i][order[k]] * sqrt(max(0, values[order[k]])) }
            return .init(x: c[0], y: c[1], z: flat ? 0 : c[2])
        }
        if ids.allSatisfy({ previous[$0] != nil }) {
            let reference = ids.map { previous[$0]! }
            // Both mirror choices have the same range evidence; select temporal continuity.
            let first = align(points, to: reference)
            let mirror = align(points.map { Vector3(x: $0.x, y: $0.y, z: -$0.z) }, to: reference)
            let error1 = zip(first, reference).reduce(0) { $0 + pow($1.0.distance(to: $1.1),2) }
            let error2 = zip(mirror, reference).reduce(0) { $0 + pow($1.0.distance(to: $1.1),2) }
            points = error1 <= error2 ? first : mirror
        }
        if flat { points = points.map { .init(x:$0.x,y:$0.y,z:0) } }
        var error = 0.0, count = 0
        for i in 0..<n { for j in (i+1)..<n {
            error += pow(points[i].distance(to: points[j]) - sqrt(squared[i][j]), 2); count += 1
        } }
        let rms = sqrt(error / Double(count))
        guard rms < 0.4 else { return nil }
        return .init(positions: Dictionary(uniqueKeysWithValues: zip(ids, points)), rms: rms,
                     heightResolved: !flat && values[order[2]] > max(0.1, values[order[0]] * 0.01))
    }

    /// Jacobi eigensolver for small real symmetric matrices; columns are eigenvectors.
    static func eigen(_ input: [[Double]]) -> ([Double], [[Double]]) {
        let n = input.count
        var a = input
        var v = (0..<n).map { i in (0..<n).map { i == $0 ? 1.0 : 0.0 } }
        for _ in 0..<(n*n*40) {
            var p = 0, q = 1, largest = 0.0
            for i in 0..<n { for j in (i+1)..<n {
                if abs(a[i][j]) > largest { largest = abs(a[i][j]); p=i; q=j }
            } }
            if largest < 1e-10 { break }
            let angle = 0.5 * atan2(2*a[p][q], a[q][q]-a[p][p])
            let c = cos(angle), s = sin(angle)
            let app = a[p][p], aqq = a[q][q], apq = a[p][q]
            for k in 0..<n where k != p && k != q {
                let akp = a[k][p], akq = a[k][q]
                a[k][p] = c*akp-s*akq; a[p][k] = a[k][p]
                a[k][q] = s*akp+c*akq; a[q][k] = a[k][q]
            }
            a[p][p] = c*c*app - 2*s*c*apq + s*s*aqq
            a[q][q] = s*s*app + 2*s*c*apq + c*c*aqq
            a[p][q] = 0; a[q][p] = 0
            for k in 0..<n {
                let vkp = v[k][p], vkq = v[k][q]
                v[k][p] = c*vkp-s*vkq; v[k][q] = s*vkp+c*vkq
            }
        }
        return ((0..<n).map { a[$0][$0] }, v)
    }

    private static func align(_ points: [Vector3], to reference: [Vector3]) -> [Vector3] {
        let n = Double(points.count)
        let pc = points.reduce(.zero,+) * (1/n), rc = reference.reduce(.zero,+) * (1/n)
        var h = Array(repeating: Array(repeating: 0.0, count: 3), count: 3)
        for (p,r) in zip(points,reference) {
            let a = p-pc, b = r-rc
            let x = [a.x,a.y,a.z], y = [b.x,b.y,b.z]
            for i in 0..<3 { for j in 0..<3 { h[i][j] += x[i]*y[j] } }
        }
        let xx=h[0][0], xy=h[0][1], xz=h[0][2], yx=h[1][0], yy=h[1][1], yz=h[1][2], zx=h[2][0], zy=h[2][1], zz=h[2][2]
        let matrix = [[xx+yy+zz, yz-zy, zx-xz, xy-yx],
                      [yz-zy, xx-yy-zz, xy+yx, zx+xz],
                      [zx-xz, xy+yx, -xx+yy-zz, yz+zy],
                      [xy-yx, zx+xz, yz+zy, -xx-yy+zz]]
        let (values,vectors) = eigen(matrix)
        let k = values.indices.max(by: { values[$0] < values[$1] })!
        let w=vectors[0][k], x=vectors[1][k], y=vectors[2][k], z=vectors[3][k]
        return points.map { p in
            let p = p-pc
            return Vector3(x: (1-2*y*y-2*z*z)*p.x + 2*(x*y-z*w)*p.y + 2*(x*z+y*w)*p.z,
                           y: 2*(x*y+z*w)*p.x + (1-2*x*x-2*z*z)*p.y + 2*(y*z-x*w)*p.z,
                           z: 2*(x*z-y*w)*p.x + 2*(y*z+x*w)*p.y + (1-2*x*x-2*y*y)*p.z) + rc
        }
    }
}
