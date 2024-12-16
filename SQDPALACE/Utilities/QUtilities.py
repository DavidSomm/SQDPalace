# from pint import UnitRegistry
import shapely
import numpy as np
import itertools
from qiskit_metal import Dict
from qiskit_metal.toolbox_python.utility_functions import bad_fillet_idxs
from qiskit_metal.qlibrary.terminations.launchpad_wb import LaunchpadWirebond
from qiskit_metal.qlibrary.terminations.open_to_ground import OpenToGround
from qiskit_metal.qlibrary.tlines.meandered import RouteMeander
from qiskit_metal.qlibrary.tlines.straight_path import RouteStraight
from qiskit_metal.analyses import cpw_calculations
from SQDMetal.Utilities.PVD_Shadows import PVD_Shadows
from SQDMetal.Utilities.QiskitShapelyRenderer import QiskitShapelyRenderer
from SQDMetal.Utilities.ShapelyEx import ShapelyEx
from SQDMetal.Comps import Markers
from SQDMetal.Utilities.QubitDesigner import ResonatorQuarterWave


class QUtilities:
    @staticmethod
    def get_units(design):
        unit_conv = design.get_units()
        if unit_conv == "mm":
            unit_conv = 1e-3
        elif unit_conv == "um":
            unit_conv = 1e-6
        elif unit_conv == "nm":
            unit_conv = 1e-9
        else:
            assert False, f"Unrecognised units: {unit_conv}"
        return unit_conv

    @staticmethod
    def parse_value_length(strVal):
        # This is far too slow!?
        # ureg = UnitRegistry().Quantity
        # return ureg(strVal).to('m').magnitude

        # So do this instead...
        if isinstance(strVal, int) or isinstance(strVal, float):
            return strVal
        strVal = strVal.strip()
        assert len(strVal) > 1, f"Length '{strVal}' is invalid (no units?)."
        if strVal[-2:] == "mm":
            return float(strVal[:-2] + "e-3")
        elif strVal[-2:] == "um":
            return float(strVal[:-2] + "e-6")
        elif strVal[-2:] == "nm":
            return float(strVal[:-2] + "e-9")
        elif strVal[-2:] == "pm":
            return float(strVal[:-2] + "e-12")
        elif strVal[-2:] == "fm":
            return float(strVal[:-2] + "e-15")
        elif strVal[-1:] == "m":
            return float(strVal[:-2])
        else:
            assert len(strVal) > 1, f"Length '{strVal}' is invalid."

    @staticmethod
    def get_comp_bounds(design, objs, units_metres=False):
        if not (isinstance(objs, list) or isinstance(objs, np.ndarray)):
            objs = [objs]
        x_vals = []
        y_vals = []
        unit_conv = QUtilities.get_units(design)
        for cur_obj in objs:
            paths = design.components[cur_obj].qgeometry_table("path")
            for _, row in paths.iterrows():
                cur_minX, cur_minY, cur_maxX, cur_maxY = (
                    row["geometry"]
                    .buffer(row["width"] / 2, cap_style=shapely.geometry.CAP_STYLE.flat)
                    .bounds
                )
                x_vals += [cur_minX, cur_maxX]
                y_vals += [cur_minY, cur_maxY]
            for cur_poly in design.components[cur_obj].qgeometry_list("poly"):
                cur_minX, cur_minY, cur_maxX, cur_maxY = cur_poly.bounds
                x_vals += [cur_minX, cur_maxX]
                y_vals += [cur_minY, cur_maxY]
            # In case the geometry is empty, just take the pos_x and pos_y identifiers (e.g. the Joint object)...
            if (
                len(design.components[cur_obj].qgeometry_table("path")) == 0
                and len(design.components[cur_obj].qgeometry_table("poly")) == 0
            ):
                x_vals += [
                    QUtilities.parse_value_length(
                        design.components[cur_obj].options.pos_x
                    )
                    / unit_conv
                ]
                y_vals += [
                    QUtilities.parse_value_length(
                        design.components[cur_obj].options.pos_y
                    )
                    / unit_conv
                ]
        if units_metres:
            return (
                unit_conv * min(x_vals),
                unit_conv * min(y_vals),
                unit_conv * max(x_vals),
                unit_conv * max(y_vals),
            )
        else:
            return min(x_vals), min(y_vals), max(x_vals), max(y_vals)

    @staticmethod
    def calc_points_on_path(
        dists,
        design,
        component_name,
        trace_name="",
        trace_name_gap="",
        dists_are_fractional=False,
    ):
        """
        Returns the points along a path taking into account the curved edges (i.e. fillets).

        Inputs:
            * dists - List of raw distances (or fractional distances from 0 to 1 if dists_are_fractional is made True) along the path. If dists
                      is given as a single value, then dists_are_fractional is ignored, and the returned path will be a bunch of points spaced
                      by the value given by dists. Units are in Qiskit-Metal design units (when not fractional).
            * component_name - Name of the QComponent in the design
            * trace_name - (Optional) If provided, then the path given by the trace_name is selected. Otherwise, the default path (typically
                           called 'trace' in normal wiring/routing objects) is selected.
            * trace_name_gap - (Optional) If provided, then the returned gap width is given by the trace_name_gap entity. Otherwise, the
                               default path (typically called 'cut' in normal wiring/routing objects) is taken for the gap width.
            * dists_are_fractional - (Defaults to True) If True, then the argument dists is taken as a list of  fractional distances from 0 to 1.

        Returns tuple (final_pts, normals, width, gap, total_dist) where:
            * final_pts - Numpy array of coordinates along the path given as (x,y) on every row.
            * normals   - Numpy array of unit-vectors for the right-hand normal vector for every point in final_pts given as (nx, ny) on every row.
            * width - CPW trace width.
            * gap   - CPW trace gap.
            * total_dist - Total length of path.
        Note that the units in the returned values are in Qiskit-Metal design units...
        """

        df = design.qgeometry.tables["path"]
        df = df[
            df["component"] == design.components[component_name].id
        ]  # ['geometry'][0]
        if trace_name != "":
            dfTrace = df[df["name"] == trace_name]
            if trace_name_gap != "":
                dfGap = df[df["name"] == trace_name_gap]
            else:
                dfGap = df[df["name"] == trace_name]
        else:
            dfTrace = df[df["subtract"] == False]
            dfGap = df[df["subtract"] == True]
        width = dfTrace["width"].iloc[0]
        if len(dfGap) > 0:
            gap = (dfGap["width"].iloc[0] - width) * 0.5
        else:
            gap = 0
        assert len(dfTrace) > 0, f"The component '{component_name}' has no valid path."

        rFillet = dfTrace["fillet"].iloc[0]
        line_segs = QUtilities.calc_lines_and_fillets_on_path(
            np.array(dfTrace["geometry"].iloc[0].coords[:]),
            rFillet,
            design.template_options.PRECISION,
        )

        total_dist = sum([x["dist"] for x in line_segs])

        if isinstance(dists, (list, tuple, np.ndarray)):
            dists = np.sort(dists)
            if dists_are_fractional:
                assert (
                    np.min(dists) >= 0 and np.max(dists) <= 1
                ), "Fractional distances must be in the interval: [0,1]."
                dists = dists * total_dist
        else:
            dists = np.arange(0, total_dist, dists)
            if dists[-1] != total_dist:
                dists = np.concatenate([dists, [total_dist]])

        final_pts = []
        normals = []
        line_seg_ind = 0
        cur_seg_dist = 0
        for cur_dist in dists:
            while cur_dist > cur_seg_dist + line_segs[line_seg_ind]["dist"]:
                cur_seg_dist += line_segs[line_seg_ind]["dist"]
                line_seg_ind += 1
            if "centre" in line_segs[line_seg_ind]:
                angleReq = (cur_dist - cur_seg_dist) / rFillet
                angleReq = line_segs[line_seg_ind]["angleStart"] + angleReq * np.sign(
                    line_segs[line_seg_ind]["angleDelta"]
                )
                norm_dir = np.array([np.cos(angleReq), np.sin(angleReq)])
                new_pt = line_segs[line_seg_ind]["centre"] + rFillet * norm_dir
                normals += [norm_dir * np.sign(line_segs[line_seg_ind]["angleDelta"])]
            else:
                new_pt = (
                    line_segs[line_seg_ind]["start"]
                    + (cur_dist - cur_seg_dist) * line_segs[line_seg_ind]["dir"]
                )
                normals += [
                    [
                        line_segs[line_seg_ind]["dir"][1],
                        -line_segs[line_seg_ind]["dir"][0],
                    ]
                ]
            final_pts += [new_pt]
        return np.array(final_pts), np.array(normals), width, gap, total_dist

    @staticmethod
    def calc_lines_and_fillets_on_path(points, rFillet, precision):
        """
        Returns the line segments and curved fillets on a given path of points.

        Inputs:
            * points - Numpy array of points given as (x,y) on every row.
            * rFillet - Radius of fillets on every corner.
            * precision - The numeric precision in which to calculate bad fillets taken usually as design.template_options.PRECISION

        The function returns list of segments each represented as dictionaries. Straight segments have the keys:
            * 'start' - Starting point of line segment
            * 'end'   - Ending point of line segment
            * 'dir'   - Unit-vector of the direction of the line segment
            * 'dist'  - Length of the line segment
        Curved segments (i.e. ones with fillets) have the keys:
            * 'start'  - Starting point of line segment
            * 'end'    - Ending point of line segment
            * 'centre' - Centre of the circle drawing the fillet arc
            * 'angleStart' - Starting angle (on Cartesian plane) of the fillet arc (in radians) on the corner (not from the centre)
            * 'angleDelta' - Angle traversed by the fillet arc (in radians) using the typical polar angle direction convention in R2...
            * 'dist'   - Arclength of the fillet
        """

        # Get list of vertices that can't be filleted
        no_fillet = bad_fillet_idxs(points, rFillet, precision)

        line_segs = []
        curPt = points[0]
        for m, (corner, end) in enumerate(zip(points[1:], points[2:])):
            a = corner - curPt
            b = end - corner
            aDist = np.linalg.norm(a)
            bDist = np.linalg.norm(b)
            aHat = a / aDist
            bHat = b / bDist
            theta = np.arccos(np.dot(-aHat, bHat))
            if m + 1 in no_fillet or np.abs(theta - np.pi) < 1e-9:
                line_segs += [
                    {
                        "start": curPt,
                        "end": corner,
                        "dir": aHat,
                        "dist": np.linalg.norm(corner - curPt),
                    }
                ]
                curPt = corner
            else:
                distTangentEdge = rFillet / np.tan(theta / 2)
                ptFilletStart = corner - aHat * distTangentEdge
                ptFilletEnd = corner + bHat * distTangentEdge

                vCentre = bHat - np.dot(aHat, bHat) * aHat
                vCentre *= rFillet / np.linalg.norm(vCentre)
                ptCentre = ptFilletStart + vCentre

                angleStart = ptFilletStart - ptCentre
                angleStart = np.arctan2(angleStart[1], angleStart[0])
                #
                if aHat[0] * bHat[1] > aHat[1] * bHat[0]:
                    angleDelta = theta
                else:
                    angleDelta = -theta

                line_segs += [
                    {
                        "start": curPt,
                        "end": ptFilletStart,
                        "dir": aHat,
                        "dist": np.linalg.norm(ptFilletStart - curPt),
                    }
                ]
                line_segs += [
                    {
                        "start": ptFilletStart,
                        "end": ptFilletEnd,
                        "centre": ptCentre,
                        "angleStart": angleStart,
                        "angleDelta": angleDelta,
                        "dist": (np.pi - theta) * rFillet,
                    }
                ]
                curPt = ptFilletEnd
        distLast = np.linalg.norm(points[-1] - curPt)
        line_segs += [
            {
                "start": curPt,
                "end": points[-1],
                "dir": (points[-1] - curPt) / distLast,
                "dist": distLast,
            }
        ]

        return line_segs

    @staticmethod
    def calc_filleted_path(points, rFillet, precision, resolution=4):
        """
        Returns the coordinates of a given path after filleting (like in Qiskit-Metal).

        Inputs:
            * points - Numpy array of points given as (x,y) on every row.
            * rFillet - Radius of fillets on every corner.
            * precision - The numeric precision in which to calculate bad fillets taken usually as design.template_options.PRECISION

        The function returns list of points (x,y) for the points defining the filleted path.
        """
        line_segs = QUtilities.calc_lines_and_fillets_on_path(
            points, rFillet, precision
        )
        print(line_segs)
        final_pts = []
        for cur_seg in line_segs:
            if "centre" in cur_seg:
                cx, cy = cur_seg["centre"]
                if cur_seg["angleDelta"] > 0:
                    phi = np.pi - cur_seg["angleDelta"]
                else:
                    phi = -(np.pi + cur_seg["angleDelta"])
                curv_pts = [
                    (rFillet * np.cos(angle) + cx, rFillet * np.sin(angle) + cy)
                    for angle in np.linspace(
                        cur_seg["angleStart"], cur_seg["angleStart"] + phi, resolution
                    )
                ]
                final_pts += curv_pts
            else:
                final_pts.append(cur_seg["start"])
                final_pts.append(cur_seg["end"])
        return np.array(final_pts)

    @staticmethod
    def get_metals_in_layer(design, layer_id, **kwargs):
        """
        Partitions unique conductors from a layer in a Qiskit-Metal design object. If the particular layer has fancy PVD evaporation steps, the added
        metallic layer will account for said steps and merge the final result. In addition, all metallic elements that are contiguous are merged into
        single blobs.

        Inputs:
            - design - Qiskit-Metal deisgn object
            - layer_id - The index of the layer from which to take the metallic polygons. If this is given as a LIST, then the metals in the specified
                         layer (0 being ground plane) will be fused and then added into COMSOL.
            - restrict_rect - (Optional) List highlighting the clipping rectangle [xmin,ymin,xmax,ymax]
            - resolution - (Optional) Defaults to 4. This is the number of points along a curved section of a path object in Qiskit-Metal.
            - threshold - (Optional) Defaults to -1. This is the threshold in metres, below which consecutive vertices along a given polygon are
                          combined into a single vertex. This simplification helps with meshing as COMSOL will not overdo the meshing. If this
                          argument is negative, the argument is ignored.
            - fuse_threshold - (Optional) Defaults to 1e-12. This is the minimum distance between metallic elements, below which they are considered
                               to be a single polygon and thus, the polygons are merged with the gap filled. This accounts for floating-point errors
                               that make adjacent elements fail to merge as a single element, due to infinitesimal gaps between them.
            - evap_mode - (Optional) Defaults to 'separate_delete_below'. These are the methods upon which to separate or merge overlapping elements
                          across multiple evaporation steps. See documentation on PVD_Shadows for more details on the available options.
            - group_by_evaporations - (Optional) Defaults to False. If set to True, if elements on a particular evaporation step are separated due
                                      to the given evap_mode, they will still be selected as a part of the same conductor (useful for example, in
                                      capacitance matrix simulations).
            - evap_trim - (Optional) Defaults to 20e-9. This is the trimming distance used in certain evap_mode profiles. See documentation on
                          PVD_Shadows for more details on its definition.
            - unit_conv - (Optional) Unit conversion factor to convert the Qiskit-Metal design units. Defaults to converting the units into metres.
            - multilayer_fuse - (Optional) Defaults to False. Flattens everything into a single layer (careful when using this with evap_mode).
            - smooth_radius - (Optional) Defaults to 0. If above 0, then the corners of the metallic surface will be smoothed via this radius. Only works
                              if multilayer_fuse is set to True.
            - ground_cutout - (Optional) MUST BE SPECIFIED if layer_id is 0. It is a tuple (x1, x2, y1, y2) for the x and y bounds

        Outputs a tuple containing:
            - metal_polys_all - All separate metallic islands
            - metal_sel_ids   - Selection indices for each of the metallic islands (mostly relevant when using group_by_evaporations)
        """
        # Fresh update on PVD profiles...
        pvd_shadows = PVD_Shadows(design)

        thresh = kwargs.get("threshold", -1)
        resolution = kwargs.get("resolution", 4)

        qmpl = QiskitShapelyRenderer(None, design, None)
        gsdf = qmpl.get_net_coordinates(resolution)

        if not isinstance(layer_id, (list, tuple)):
            layer_id = [layer_id]

        metal_evap_polys = []
        fuse_threshold = kwargs.get("fuse_threshold", 1e-12)
        unit_conv = kwargs.get("unit_conv", QUtilities.get_units(design))
        for cur_layer_id in layer_id:
            if cur_layer_id > 0:
                filt = gsdf.loc[
                    (gsdf["layer"] == cur_layer_id) & (gsdf["subtract"] == False)
                ]
                if filt.shape[0] == 0:
                    continue
                # Merge the metallic elements
                metal_polys = shapely.unary_union(
                    filt["geometry"].buffer(0)
                )  # Buffer makes geometry valid to prevent this error: https://stackoverflow.com/questions/74779301/unable-to-assign-free-hole-to-a-shell-error-when-flattening-polygons
                metal_polys = shapely.affinity.scale(
                    metal_polys, xfact=unit_conv, yfact=unit_conv, origin=(0, 0)
                )
                metal_polys = ShapelyEx.fuse_polygons_threshold(
                    metal_polys, fuse_threshold
                )
            else:
                filt = gsdf.loc[gsdf["subtract"] == True]
                if filt.shape[0] == 0:
                    continue
                space_polys = shapely.unary_union(filt["geometry"].buffer(0))
                space_polys = shapely.affinity.scale(
                    space_polys, xfact=unit_conv, yfact=unit_conv, origin=(0, 0)
                )
                space_polys = ShapelyEx.fuse_polygons_threshold(
                    space_polys, fuse_threshold
                )
                assert (
                    "ground_cutout" in kwargs
                ), "Must supply ground_cutout if specifying layer 0."
                xL, xR, yL, yR = kwargs.get("ground_cutout")
                poly_sheet = shapely.Polygon([[xL, yL], [xR, yL], [xR, yR], [xL, yR]])
                space_whole = shapely.unary_union(space_polys)
                #
                metal_polys = poly_sheet.difference(space_whole)

            restrict_rect = kwargs.get("restrict_rect", None)
            if isinstance(restrict_rect, list):
                metal_polys = shapely.clip_by_rect(metal_polys, *restrict_rect)
            # Calculate the individual evaporated elements if required
            evap_mode = kwargs.get("evap_mode", "separate_delete_below")
            group_by_evaporations = kwargs.get("group_by_evaporations", False)
            if group_by_evaporations and evap_mode != "merge":
                metal_evap_polys_separate = pvd_shadows.get_all_shadows(
                    metal_polys, cur_layer_id, "separate"
                )
                # Convert all MultiPolygons into individual polygons...
                if not isinstance(metal_evap_polys_separate, list):
                    metal_evap_polys_separate = [metal_evap_polys_separate]
            # Calculate evaporated shadows
            evap_trim = kwargs.get("evap_trim", 20e-9)
            metal_evap_polys += [
                pvd_shadows.get_all_shadows(
                    metal_polys, cur_layer_id, evap_mode, layer_trim_length=evap_trim
                )
            ]
        if len(metal_evap_polys) == 0:
            return
        if kwargs.get("multilayer_fuse", False):
            metal_evap_polys = ShapelyEx.fuse_polygons_threshold(
                metal_evap_polys, fuse_threshold
            )
            #
            rndDist = kwargs.get("smooth_radius", 0)
            if rndDist > 0:
                metal_evap_polys = (
                    metal_evap_polys.buffer(
                        rndDist * 0.5, join_style=1, quad_segs=resolution
                    )
                    .buffer(-rndDist, join_style=1)
                    .buffer(rndDist * 0.5, join_style=1, quad_segs=resolution)
                )
            metal_evap_polys = [metal_evap_polys]

        metal_polys_all = []
        metal_sel_ids = []
        for m, cur_poly in enumerate(metal_evap_polys):
            if isinstance(cur_poly, shapely.geometry.multipolygon.MultiPolygon):
                temp_cur_metals = [x for x in cur_poly.geoms]
                metal_polys_all += temp_cur_metals
                num_polys = len(temp_cur_metals)
            else:
                temp_cur_metals = [
                    cur_poly
                ]  # i.e. it's just a lonely Polygon object...
                metal_polys_all += temp_cur_metals
                num_polys = 1

            if group_by_evaporations and evap_mode != "merge":
                # Collect the separate polygons that live in the current evaporation layer
                cur_polys_separate = metal_evap_polys_separate[m]
                if isinstance(
                    cur_polys_separate, shapely.geometry.multipolygon.MultiPolygon
                ):
                    cur_polys_separate = [x for x in cur_polys_separate.geoms]
                else:
                    cur_polys_separate = [cur_polys_separate]
                # Find the separate polygon in which the given polygon fits...
                cur_sel_inds = []
                for cur_metal in temp_cur_metals:
                    for sep_piece_ind, cur_metal_piece in enumerate(cur_polys_separate):
                        if (
                            shapely.intersection(cur_metal, cur_metal_piece).area
                            >= 0.99 * cur_metal.area
                        ):
                            cur_sel_inds += [len(metal_sel_ids) + sep_piece_ind]
                            break
                metal_sel_ids += cur_sel_inds
            else:
                # Just enumerate to all separate metallic pieces...
                metal_sel_ids += [
                    x for x in range(len(metal_sel_ids), len(metal_sel_ids) + num_polys)
                ]

        return metal_polys_all, metal_sel_ids

    @staticmethod
    def get_perimetric_polygons(design, comp_names, **kwargs):
        thresh = kwargs.get("threshold", -1)
        resolution = kwargs.get("resolution", 4)

        qmpl = QiskitShapelyRenderer(None, design, None)
        gsdf = qmpl.get_net_coordinates(resolution)

        ids = [design.components[x].id for x in comp_names]
        filt = gsdf[gsdf["component"].isin(ids)]

        if filt.shape[0] == 0:
            return

        unit_conv = kwargs.get("unit_conv", QUtilities.get_units(design))
        fuse_threshold = kwargs.get("fuse_threshold", 1e-12)

        metal_polys = shapely.unary_union(filt["geometry"])
        metal_polys = shapely.affinity.scale(
            metal_polys, xfact=unit_conv, yfact=unit_conv, origin=(0, 0)
        )
        metal_polys = ShapelyEx.fuse_polygons_threshold(metal_polys, fuse_threshold)
        restrict_rect = kwargs.get("restrict_rect", None)
        if isinstance(restrict_rect, list):
            metal_polys = shapely.clip_by_rect(metal_polys, *restrict_rect)

        if isinstance(metal_polys, shapely.geometry.multipolygon.MultiPolygon):
            lePolys = list(metal_polys.geoms)
        else:
            lePolys = [metal_polys]

        return lePolys

    @staticmethod
    def _get_LauncherWB_params(design, launcher_name, unit_conv_extra=1):
        launcher_len = (
            QUtilities.parse_value_length(
                design.components[launcher_name].options["pad_height"]
            )
            + QUtilities.parse_value_length(
                design.components[launcher_name].options["taper_height"]
            )
            + QUtilities.parse_value_length(
                design.components[launcher_name].options["lead_length"]
            )
        )
        unit_conv = QUtilities.get_units(design)
        startPt = (
            design.components[launcher_name].pins["tie"]["middle"] * unit_conv
            - design.components[launcher_name].pins["tie"]["normal"] * launcher_len
        )
        padDir = design.components[launcher_name].pins["tie"]["normal"] * 1.0
        padWid = QUtilities.parse_value_length(
            design.components[launcher_name].options["pad_width"]
        )
        padGap = QUtilities.parse_value_length(
            design.components[launcher_name].options["pad_gap"]
        )

        return (
            startPt * unit_conv_extra,
            padDir,
            padWid * unit_conv_extra,
            padGap * unit_conv_extra,
        )

    @staticmethod
    def _get_Route_params(design, route_name, pin_name, unit_conv_extra=1):
        unit_conv = QUtilities.get_units(design)

        startPt = design.components[route_name].pins[pin_name]["middle"] * unit_conv
        padDir = -1.0 * design.components[route_name].pins[pin_name]["normal"]
        padWid = QUtilities.parse_value_length(
            design.components[route_name].options.trace_width
        )
        padGap = QUtilities.parse_value_length(
            design.components[route_name].options.trace_gap
        )

        return (
            startPt * unit_conv_extra,
            padDir,
            padWid * unit_conv_extra,
            padGap * unit_conv_extra,
        )

    @staticmethod
    def get_RFport_CPW_coords_Launcher(
        design, qObjName, len_launch=20e-6, unit_conv_extra=1
    ):
        qObj = design.components[qObjName]
        if isinstance(qObj, LaunchpadWirebond):
            vec_ori, vec_launch, cpw_wid, cpw_gap = QUtilities._get_LauncherWB_params(
                design, qObjName, unit_conv_extra
            )
        else:
            assert False, f"'{qObjName}' is an unsupported object type."

        vec_perp = np.array([vec_launch[1], -vec_launch[0]])
        vec_launch *= len_launch

        launchesA = [
            vec_ori + vec_perp * cpw_wid * 0.5,
            vec_ori + vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch + vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch + vec_perp * cpw_wid * 0.5,
        ]
        launchesA = [[p[0], p[1]] for p in launchesA]

        launchesB = [
            vec_ori - vec_perp * cpw_wid * 0.5,
            vec_ori - vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch - vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch - vec_perp * cpw_wid * 0.5,
        ]
        launchesB = [[p[0], p[1]] for p in launchesB]

        return launchesA, launchesB, vec_perp

    @staticmethod
    def get_RFport_CPW_coords_Route(
        design, qObjName, pin_name, len_launch=20e-6, unit_conv_extra=1
    ):
        qObj = design.components[qObjName]

        # TODO: Add in type-checking somehow here?
        vec_ori, vec_launch, cpw_wid, cpw_gap = QUtilities._get_Route_params(
            design, qObjName, pin_name, unit_conv_extra
        )

        vec_perp = np.array([vec_launch[1], -vec_launch[0]])
        vec_launch *= len_launch

        launchesA = [
            vec_ori + vec_perp * cpw_wid * 0.5,
            vec_ori + vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch + vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch + vec_perp * cpw_wid * 0.5,
        ]
        launchesA = [[p[0], p[1]] for p in launchesA]

        launchesB = [
            vec_ori - vec_perp * cpw_wid * 0.5,
            vec_ori - vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch - vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori + vec_launch - vec_perp * cpw_wid * 0.5,
        ]
        launchesB = [[p[0], p[1]] for p in launchesB]

        return launchesA, launchesB, vec_perp

    @staticmethod
    def get_RFport_CPW_groundU_Launcher_inplane(
        design,
        qObjName,
        thickness_side=20e-6,
        thickness_back=20e-6,
        separation_gap=0e-6,
        unit_conv_extra=1,
    ):
        qObj = design.components[qObjName]
        if isinstance(qObj, LaunchpadWirebond):
            vec_ori, vec_launch, cpw_wid, cpw_gap = QUtilities._get_LauncherWB_params(
                design, qObjName, unit_conv_extra
            )
        else:
            assert False, f"'{qObjName}' is an unsupported object type."

        vec_perp = np.array([-vec_launch[1], vec_launch[0]])

        gap_sep = separation_gap * unit_conv_extra if separation_gap > 0 else cpw_gap

        Uclip = [
            vec_ori + vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori
            + vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra),
            vec_ori
            + vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra)
            - vec_launch * (gap_sep + thickness_back * unit_conv_extra),
            vec_ori
            - vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra)
            - vec_launch * (gap_sep + thickness_back * unit_conv_extra),
            vec_ori
            - vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra),
            vec_ori - vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori - vec_perp * (cpw_wid * 0.5 + cpw_gap) - vec_launch * gap_sep,
            vec_ori + vec_perp * (cpw_wid * 0.5 + cpw_gap) - vec_launch * gap_sep,
        ]

        return Uclip

    @staticmethod
    def get_RFport_CPW_groundU_Route_inplane(
        design,
        route_name,
        pin_name,
        thickness_side=20e-6,
        thickness_back=20e-6,
        separation_gap=0e-6,
        unit_conv_extra=1,
    ):
        # qObj = design.components[route_name]
        # TODO: Do type-checking here?
        vec_ori, vec_launch, cpw_wid, cpw_gap = QUtilities._get_Route_params(
            design, route_name, pin_name, unit_conv_extra
        )
        # else:
        #     assert False, f"\'{qObjName}\' is an unsupported object type."

        vec_perp = np.array([-vec_launch[1], vec_launch[0]])

        gap_sep = separation_gap * unit_conv_extra if separation_gap > 0 else cpw_gap

        Uclip = [
            vec_ori + vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori
            + vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra),
            vec_ori
            + vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra)
            - vec_launch * (gap_sep + thickness_back * unit_conv_extra),
            vec_ori
            - vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra)
            - vec_launch * (gap_sep + thickness_back * unit_conv_extra),
            vec_ori
            - vec_perp * (cpw_wid * 0.5 + cpw_gap + thickness_side * unit_conv_extra),
            vec_ori - vec_perp * (cpw_wid * 0.5 + cpw_gap),
            vec_ori - vec_perp * (cpw_wid * 0.5 + cpw_gap) - vec_launch * gap_sep,
            vec_ori + vec_perp * (cpw_wid * 0.5 + cpw_gap) - vec_launch * gap_sep,
        ]

        return Uclip

    @staticmethod
    def calc_die_coords(chip_dim, die_dim, num_die):
        """
        Calculates centre coordinates (x, y) of multiple die on a chip.

        Inputs:
            - chip_dim - tuple containing chip dimensions (x, y) strings with units (Qiskit Metal style - e.g. "20mm")
            - die_dim - tuple containing die dimensions (x, y) strings with units (Qiskit Metal style - e.g. "4.0mm")
            - num_die - tuple containing the number of die (num_x, num_y) in x and y

        Output:
            - die_coords_sorted - a list of tuples of floats containing all die centre positions (relative to (0,0) in the chip centre) in units of meters. Sorted, ascending up each column from bottom left to top right.
        """

        # check input data is in correct format of (x, y)
        assert len(chip_dim) == len(die_dim) == len(num_die) == 2

        # calculate minimum patterened x and y coordinates
        xy_min = (
            -0.5 * (num_die[0]) * QUtilities.parse_value_length(die_dim[0]),
            -0.5 * (num_die[1]) * QUtilities.parse_value_length(die_dim[1]),
        )

        # calculate die centres
        die_coords = []
        for i, k in itertools.product(range(num_die[0]), range(num_die[1])):
            die_coords.append(
                (
                    xy_min[0] + (QUtilities.parse_value_length(die_dim[0]) * (0.5 + i)),
                    xy_min[1] + (QUtilities.parse_value_length(die_dim[1]) * (0.5 + k)),
                )
            )

        assert len(die_coords) == (
            num_die[0] * num_die[1]
        )  # check coordinate list matches number of die

        # sort from top left, vertical down, end on bottom right
        die_coords_sorted = sorted(die_coords, key=lambda tup: (tup[0],tup[1]))

        return die_coords_sorted

    @staticmethod
    def place_launchpads(
        design,
        cpw_gap,
        cpw_width,
        die_origin,
        die_dimension,
        die_number=None,
        dimension="600um",
        inset="0um",
        taper="300um",
        lead_length="25um",
        print_checks=True,
    ):
        """
        Function for calculating launchpad parameters, placing launchpads on the design, and returning launchpad objects as a tuple containing the left and right launchpads of each die as (lp_L, lp_R). Using Qiskit Metal's `LaunchpadWirebonds`.

        Inputs:
            - design - Qiskit Metal design object
            - cpw_gap - transmission line gap as string with units (e.g. "5um")
            - cpw_width - transmission line central conductor width as string with units (e.g. "5um")
            - die_origin - centre coordinates of die as tuple (float(x), float(y)) in units of meters
            - die dimension - tuple containing die dimensions (x, y) in units of meters
            - die_number - die number (int) for launchpad object naming (defaults to None)
            - dimension - horizontal length of launchpad as string with units (defaults to "600um")
            - inset - distance from edge of die to start of launchpad gap in x as a string with units (defaults to "0um")
            - taper - length of taper from launchpad to transmission line as a string with units (defaults to "300um")
            - print_checks - flag if user wants to print statements after launchpads are written to design (defaults to True)

        Output:
            - launchpad_objects - tuple containing the LaunchpadWirebond objects (lp_L, lp_R)
        """

        # check inputs
        for i in [dimension, inset, taper, cpw_gap, cpw_width, lead_length]:
            assert isinstance(i, str)
        assert isinstance(die_origin, (tuple, list)) and len(die_origin) == 2
        for i in die_origin:
            assert isinstance(i, (int, float))

        # check that user has passed a die number
        if die_number == None:
            print(
                f"No die number was passed - assuming you have only a single die! If this is not true, please retry with die_number as an input."
            )

        # calculate launchpad scaling
        lp_scale_factor = QUtilities.parse_value_length(
            dimension
        ) / QUtilities.parse_value_length(cpw_width)
        lp_width = QUtilities.parse_value_length(cpw_width) * lp_scale_factor
        lp_gap = QUtilities.parse_value_length(cpw_gap) * lp_scale_factor

        # calculate launchpad positions relative to chip origin
        lp_x = (
            die_origin[0]
            + (QUtilities.parse_value_length(die_dimension[0]) / 2)
            - QUtilities.parse_value_length(inset)
            - QUtilities.parse_value_length(dimension)
            - QUtilities.parse_value_length(taper)
            - QUtilities.parse_value_length(lp_gap)
        )

        lp_x_L = (
            die_origin[0]
            - (QUtilities.parse_value_length(die_dimension[0]) / 2)
            + QUtilities.parse_value_length(inset)
            + QUtilities.parse_value_length(dimension)
            + QUtilities.parse_value_length(taper)
            + QUtilities.parse_value_length(lp_gap)
        )

        # set launchpad options
        lp_L_ops = Dict(
            pos_x=f"{(lp_x_L * 1e6)}um",
            pos_y=f"{die_origin[1] * 1e6}um",
            pad_width=f"{lp_width * 1e6}um",
            pad_height=dimension,
            pad_gap=f"{lp_gap * 1e6}um",
            trace_width=cpw_width,
            trace_gap=cpw_gap,
            taper_height=taper,
            lead_length=lead_length,
        )

        lp_R_ops = Dict(
            pos_x=f"{lp_x * 1e6}um",
            pos_y=f"{die_origin[1] * 1e6}um",
            orientation="-180",
            pad_width=f"{lp_width * 1e6}um",
            pad_height=dimension,
            pad_gap=f"{lp_gap * 1e6}um",
            trace_width=cpw_width,
            trace_gap=cpw_gap,
            taper_height=taper,
            lead_length=lead_length,
        )

        # place launchpads
        lp_L = LaunchpadWirebond(design, f"lp_L_die{die_number}", options=lp_L_ops)
        lp_R = LaunchpadWirebond(design, f"lp_R_die{die_number}", options=lp_R_ops)

        # print statement after placing
        if print_checks:
            if die_number is not None:
                print(
                    f"Launchpads placed on die {die_number} at \t({die_origin[0] * 1e6:.0f}, {die_origin[1] * 1e6:.0f})\t[µm] \tnames: ({lp_L.name}, {lp_R.name})"
                )
            else:
                print(f"Launchpads placed: ({lp_L.name}, {lp_R.name})")

        launchpad_objects = (lp_L, lp_R)

        return launchpad_objects

    @staticmethod
    def place_resonators_hanger(
        design,
        gap,
        width,
        num_resonators,
        frequencies,
        die_origin,
        die_dimension,
        die_index,
        launchpad_extent,
        film_thickness="100nm",
        coupling_gap="20um",
        transmission_line_y="0um",
        launchpad_to_res="200um",
        res_shift_x="225um",
        min_res_gap="150um",
        res_vertical="1500um",
        chip_name="main",
        LC_calculations=True,
        print_statements=True,
        fillet="85um"
    ):
        """
        Function for placing multiple hanger-mode quarter-wavelength resonators (coupled end open-terminated with 10um ground pocket) coupled to a shared transmission line on a multi-die chip. Resonator length, start and end position are automatically calculated based on frequency, number of die, number of resonators, and launchpad properties. The function returns generated resonator names, and optionally, resonator capicatance and inductance. Clear print-out statements are also made.

        Inputs:
            - design - Qiskit Metal design object
            - gap - transmission line gap as string with units (e.g. "5um")
            - width - transmission line central conductor width as string with units (e.g. "5um")
            - num_resonators - number of resonators to be placed on each die
            - frequencies - list of frequencies in Hz (must be same length as num_resonators)
            - die_origin - centre coordinates of die as tuple (float(x), float(y)) in units of meters
            - die dimension - tuple containing die dimensions (x, y) in units of meters
            - die_index - die number (int) for resonator object naming
            - launchpad_extent - total width of the launchpad including inset from edge of die in units of meters
        Inputs (optional):
            - film thickness - as a string with units (defaults to "100nm")
            - coupling gap - amount of ground plane between transmission line and resonator coupling length as a string with units (defaults to "20um")
            - transmission_line_y - y-position of tranmission line in relation to die centre as a string with units (defaults to "0um")
            - launchpad_to_res - minimum horizontal gap between resonator start pin and the launchpad connector as a string with units (defaults to "200um")
            - res_shift_x - amount to shift resonators along the feedline as a string with units (to be used for manually adjusting resonator position, defaults to "225um")
            - min_res_gap - minimum gap between resonators as a string with units (defaults to "150um")
            - res_vertical - vertical extent of resonator meanders as a string with units (defaults to "1500um")
            - chip name - defaults to "main"
            - LC_calculation - boolean to activate capicatance and inductance calculations and outputs (defaults to True)
            - print_statements - boolean to activate print statements containing resonator names, lengths and frequencies (defaults to True)
            - fillet - choose fillet size as string with units (defaults to "100um")
        Outputs:
            - resonators - list containing resonator QComponents
            - resontor_vals - list containing 3-tuples of (length, effective permittivity, filling factor)
            - resonator_names - list containing resonator names as strings
        Outputs (optional, if LC_calculation = True):
            - capacitances - list containing calculated capacitances of resonators
            - inductances - list containing calculated inductances of resonators
        """

        # check inputs
        assert num_resonators == len(frequencies)
        assert isinstance(launchpad_extent, (float, int))
        for i in [
            gap,
            width,
            die_dimension[0],
            die_dimension[1],
            transmission_line_y,
            launchpad_to_res,
            min_res_gap,
            coupling_gap,
            film_thickness
        ]:
            assert isinstance(i, str)
        for i in [die_origin, die_dimension]:
            assert isinstance(i, (tuple, list))
        assert len(die_origin) == 2 and len(die_dimension) == 2
        assert isinstance(die_index, int)

        # parse strings to float values
        w = QUtilities.parse_value_length(width)
        g = QUtilities.parse_value_length(gap)
        h = np.abs(QUtilities.parse_value_length(design.chips[chip_name].size['size_z']))
        ft = QUtilities.parse_value_length(film_thickness)
        tl_y = QUtilities.parse_value_length(transmission_line_y)
        cg = QUtilities.parse_value_length(coupling_gap)
        x0 = QUtilities.parse_value_length(die_origin[0])
        y0 = QUtilities.parse_value_length(die_origin[1])

        # calculate x-projected length of useable transmission line [m]
        tl_extent = (
            QUtilities.parse_value_length(die_dimension[0])
            - (2 * launchpad_extent)
            - (2 * QUtilities.parse_value_length(launchpad_to_res))
        )

        # calculate width per resonator (according to num_resonators)
        x_increment_res = (tl_extent / num_resonators) + QUtilities.parse_value_length(min_res_gap)

        if print_statements: print(f'\nNOW PRINTING: Die {die_index + 1}\n')

        # initialise lists
        x_positions, resonator_names, resonator_vals = [], [], []
        if LC_calculations: capacitances, inductances = [], []

        for i in range(num_resonators):
            # name resonators
            resonator_names.append(f"die{die_index}_res{i+1}_{frequencies[i] * 1e-9:.2f}GHz")

        resonators = []
        for i, res in enumerate(resonator_names):

            # calculate x_position and add to list
            x_pos_cur = x0 - (tl_extent / 2) + (i * x_increment_res) + QUtilities.parse_value_length(res_shift_x)
            x_positions.append(x_pos_cur)

            # calculate capacitance and inductance if requested
            if LC_calculations:
                r_cur = ResonatorQuarterWave(f0=frequencies[i], impedance=50)
                capacitances.append(r_cur.get_res_capacitance())
                inductances.append(r_cur.get_res_capacitance())

            # calculate length based on frequency
            l_fullwave, er_eff, F = cpw_calculations.guided_wavelength(
                freq=frequencies[i],
                line_width=w,
                line_gap=g,
                substrate_thickness=h,
                film_thickness=ft,
            )

            # calculate length for quarter-wave resonator
            l_quarterwave_mm = f'{(l_fullwave / 4) * 1e3:.2f}mm'

            # store resonator values (length, effective permittivity, filling factor)
            resonator_vals.append([l_fullwave, er_eff, F])

            # calculate y value of resonator start
            res_y_val = (y0 + tl_y - cg - w - 2*g)
            res_y_um = f'{res_y_val * 1e6:.3f}um'

            # calculate y value of resonator end
            res_end_y_um = f'{(res_y_val - QUtilities.parse_value_length(res_vertical)) * 1e6:.3f}um'

            # strings for x position
            res_x_um = f'{x_pos_cur * 1e6:.3f}um'

            # printouts
            if print_statements: 
                print(f'Resonator {i+1}: {res}')
                print(f'\tl: {l_quarterwave_mm}')
                print(f'\tf: {frequencies[i] * 1e-9:.2f}GHz')
            
            # create pin component for start-point (open, 10um pocket)
            res_start_pin = OpenToGround(
                design,
                res + "_startPin",
                options=Dict(
                    pos_x=res_x_um, 
                    pos_y=res_y_um, 
                    width=width, 
                    gap=gap, 
                    termination_gap="10um", 
                    orientation="180"
                )
            )

            # create pin component for start-point (shorted)
            res_end_pin = OpenToGround(
                design,
                res + "_endPin",
                options=Dict(
                    pos_x=res_x_um, 
                    pos_y=res_end_y_um, 
                    width=width, 
                    gap=gap, 
                    termination_gap="0um", 
                    orientation="-90"
                )
            )

            # get pin ids
            start_pin_id = next(iter(res_start_pin.pin_names))
            end_pin_id = next(iter(res_end_pin.pin_names))

            # set resonator options
            res_options = Dict(
                            total_length=l_quarterwave_mm, 
                            constr_radius="100um",
                            constr_width_max="0um",
                            trace_width=width, 
                            trace_gap=gap,
                            fillet=fillet,
                            fillet_padding="10um",
                            start_left=True,
                            layer='1',
                            pin_inputs = Dict(
                                    start_pin=Dict(component=res + "_startPin", 
                                                pin=start_pin_id),
                                    end_pin=Dict(component=res + "_endPin",     
                                                pin=end_pin_id))
                            )
            
            # make resonator
            res = RouteMeander(
                design,
                res,
                options=Dict(**res_options)
                )
            
            resonators.append(res)
    
        if print_statements: print('\n')

        # sanity check and outputs
        # TODO: embed capacitances and inductances in resonator_vals
        if LC_calculations: 
            assert len(capacitances)==len(inductances)==num_resonators
            return resonators, resonator_vals, resonator_names, capacitances, inductances
        else:
            return resonators, resonator_vals, resonator_names
    
    @staticmethod
    def place_transmission_line_from_launchpads(design, tl_y, gap, width, die_index):
        """
        Function to place transmission lines between launchpad pins on a multi-die chip. Each transmission line is named as "tl_die{die_index}".

        Inputs:
            - design - qiskit metal design
            - tl_y - y coordinate for the primary routing of the transmission line, relative to the die centre as a string with units
            - gap - transmission line gap as a string with units
            - width - transmission line width as a string with units
            - die_index - index of the current die for QComponent naming
        
        Outputs:
            - tl - transmission line QComponent
        """

        # TODO: add automatic component/pin naming from launchpad list which should be passed as an input

        # draw straight tranmission line if in the center of chip vertically (lining up with launchpads)
        if tl_y in ["0nm", "0um", "0mm", "0cm", "0m"]:
            tl_options = Dict(
                pin_inputs=Dict(
                    start_pin=Dict(component=f'lp_L_die{die_index+1}', pin='tie'),
                    end_pin=Dict(component=f'lp_R_die{die_index+1}', pin='tie'),
                ),
                trace_width=width,
                trace_gap=gap,
            )
            tl = RouteStraight(design=design, 
                            name=f"tl_die{die_index}", 
                            options=tl_options)
        elif tl_y not in ["0nm", "0um", "0mm", "0cm", "0m"]:
            raise Exception("Sorry, for now the transmission line must be at y=0um. Functionality for other placements coming soon")

            

        return tl
    
    @staticmethod
    def place_markers(design, die_origin, die_dim, marker_type="cross"):
        """
        Function to place dicing markers on a multi-die chip.

        Inputs:
            - design - qiskit metal design
            - die_origin - tuple containing die origin (x, y) as float in meters
            - die_dim - die dimensionsion as a yuple (x, y) containing strings with units
        Input (optional):
            - marker_type - marker type, currently only supports cross markers (defaults to "cross")
        """

        # get values for die dimensions
        dx = QUtilities.parse_value_length(die_dim[0])
        dy = QUtilities.parse_value_length(die_dim[1])

        # get die origin
        x0, y0 = die_origin[0], die_origin[1]

        # marker coordinates
        l_x = f'{(x0 - dx/2) * 1e3}mm'
        r_x = f'{(x0 + dx/2) * 1e3}mm'
        b_y = f'{(y0 - dy/2) * 1e3}mm'
        t_y = f'{(y0 + dy/2) * 1e3}mm'

        if marker_type=="cross" or "Cross":
            # place markers
            Markers.MarkerDicingCross(design, options=Dict(pos_x=l_x, pos_y=b_y)).make()
            Markers.MarkerDicingCross(design, options=Dict(pos_x=r_x, pos_y=b_y)).make()
            Markers.MarkerDicingCross(design, options=Dict(pos_x=l_x, pos_y=t_y)).make()
            Markers.MarkerDicingCross(design, options=Dict(pos_x=r_x, pos_y=t_y)).make()
        else: 
            raise Exception("Only marker_type=\"cross\" is currently available.")
            # TODO: add other marker types as options