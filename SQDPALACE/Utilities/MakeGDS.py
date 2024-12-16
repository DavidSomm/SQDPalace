import gdspy
import shapely
import numpy as np
from datetime import datetime
from types import NoneType
from SQDMetal.Utilities.QUtilities import QUtilities
from SQDMetal.Utilities.QiskitShapelyRenderer import QiskitShapelyRenderer
from SQDMetal.Utilities.ShapelyEx import ShapelyEx

class MakeGDS:
    def __init__(self, design, threshold=1e-9, precision=1e-9, curve_resolution=22, smooth_radius=0, export_type='all', export_layers=None):
        '''
        Inputs:
            design      - The QiskitMetal design object...
            threshold   - The smallest feature width that can exist; anything smaller will get culled out. This is to help remove
                          artefacts from floating-point inaccuracies. It is given in metres.
            precision   - The GDS granularity/precision given in metres.
            curve_resolution - Number of vertices to use on a path fillet
            export_type - 'all' (exports positive and negative patterns and GND plane as seperate layers), 
                          'positive' (exports flattened metals to layer 0), or 
                          'negative' (exports negative flattened metals to layer 0)
            export_layers - layers to export as a list of integers or single integer (WIP)
        '''
        self._design = design
        self.curve_resolution = curve_resolution
        self.threshold = threshold
        self.precision = precision
        self.smooth_radius = smooth_radius
        self.gds_units = 1e-6
        self.export_type = export_type
        self.export_layers = export_layers
        self.refresh()

    def _shapely_to_gds(self, metals, layer):
        if isinstance(metals, shapely.geometry.multipolygon.MultiPolygon):
            temp_cur_metals = [x for x in metals.geoms]
        else:
            temp_cur_metals = [metals]
        gds_metal = None
        for cur_poly in temp_cur_metals:
            if gds_metal:
                gds_metal = gdspy.boolean(gdspy.Polygon(cur_poly.exterior.coords[:]), gds_metal, "or", layer=layer, max_points=0)
            else:
                gds_metal = gdspy.Polygon(cur_poly.exterior.coords[:], layer=layer)
            for cur_int in cur_poly.interiors:
                gds_metal = gdspy.boolean(gds_metal,gdspy.Polygon(cur_int.coords[:]), "not", layer=layer, max_points=0)
        return gds_metal

    def _round_corners(self, gds_metal, output_layer):
        #Perform the usual expand and contract to join fractures... But this fails due to some gdsPy bugs?
        # new_metal = gdspy.boolean(gdspy.offset(new_metal, self.threshold/self.gds_units, max_points=0, join_first=True), None, "or", max_points=0)
        # new_metal = gdspy.boolean(gdspy.offset(new_metal, -self.threshold/self.gds_units, max_points=0, join_first=True), None, "or", max_points=0)
        # new_metal = new_metal.fillet(self.smooth_radius/self.gds_units, max_points=0)

        #So use Shapely instead...
        polyFuse = ShapelyEx.fuse_polygons_threshold([shapely.Polygon(x) for x in gds_metal.polygons], self.threshold/self.gds_units)
        rndDist = self.smooth_radius/self.gds_units
        polyFuse = polyFuse.buffer(rndDist*0.5, join_style=1, quad_segs=self.curve_resolution).buffer(-rndDist, join_style=1).buffer(rndDist*0.5, join_style=1, quad_segs=self.curve_resolution)
        new_metal = self._shapely_to_gds(polyFuse, output_layer)
        return new_metal

    def refresh(self):
        '''
        Deletes everything and rebuilds metallic layers from scratch with the ground plane being in layer zero. The negative versions of the layers are given in layers starting from highest layer index plus 10. Export all layers (negative plus positive patterns), or only positive or negative patterns.
        '''
        assert self.export_type in ["all", "negative", "positive"], "Export type must be: \"all\", \"negative\", or \"positive\""

        qmpl = QiskitShapelyRenderer(None, self._design, None)
        gsdf = qmpl.get_net_coordinates(self.curve_resolution)

        gdspy.current_library.cells.clear()

        leUnits = QUtilities.get_units(self._design)
        self.unit_conv = leUnits/self.gds_units
        self.lib = gdspy.GdsLibrary()   #precision=self.precision - disabling it for now; there is some bug in this argument...
        self.cell = self.lib.new_cell('Main')
        threshold = self.threshold
        unit_conv = self.unit_conv

        #Assemble geometry for each layer
        all_layers = []
        self._layer_metals = {}
        #Assuming Layer 0 is GND Plane
        gaps = gsdf.loc[(gsdf['subtract'] == True)]
        gaps = ShapelyEx.fuse_polygons_threshold(gaps['geometry'].tolist(), threshold)
        #
        sx = QUtilities.parse_value_length(self._design.chips['main']['size']['size_x'])/leUnits
        sy = QUtilities.parse_value_length(self._design.chips['main']['size']['size_y'])/leUnits
        cx, cy = [QUtilities.parse_value_length(self._design.chips['main']['size'][x])/leUnits for x in ['center_x', 'center_y']]
        self.sx, self.sy, self.cx, self.cy = sx, sy, cx, cy
        #
        rect = shapely.Polygon([(cx-0.5*sx, cy-0.5*sy), (cx+0.5*sx, cy-0.5*sy), (cx+0.5*sx, cy+0.5*sy), (cx-0.5*sx, cy+0.5*sy), (cx-0.5*sx, cy-0.5*sy)])
        self.rect = rect
        chip = rect.difference(gaps)
        chip = shapely.affinity.scale(chip, xfact=unit_conv, yfact=unit_conv, origin=(0,0))
        # add GND plane to all_layers with index 0
        all_layers.append((0, chip))
        #
        #
        leLayers = np.unique(gsdf['layer'])

        print(f'Export type: {self.export_type}\n')

        # create layer list (positive layers)
        for layer in leLayers:
            metals = gsdf.loc[(gsdf['layer'] == layer) & (gsdf['subtract'] == False)]
            metals = ShapelyEx.fuse_polygons_threshold(metals['geometry'].tolist(), threshold)
            metals = shapely.affinity.scale(metals, xfact=unit_conv, yfact=unit_conv, origin=(0,0))
            all_layers.append((layer, metals))

        # write layers based on export options
        neg_layer_offset = np.max(leLayers)+10
        for cur_layer in all_layers:
            # 0: ground, 1: fused metals (i.e. centre conductor)
            layer, metals = cur_layer
            gds_metal = self._shapely_to_gds(metals, layer)    
            if self.smooth_radius > 0:
                gds_metal = self._round_corners(gds_metal, layer)

            # negative layer
            if self.export_type in ("negative", "all"):        
                gds_rect_neg = gdspy.boolean(gdspy.Rectangle(((cx-0.5*sx)*unit_conv, 
                                                          (cy-0.5*sy)*unit_conv),
                                                          ((cx+0.5*sx)*unit_conv, 
                                                           (cy+0.5*sy)*unit_conv)),
                                                        gds_metal, 
                                                        "not", 
                                                        layer=layer+neg_layer_offset, 
                                                        max_points=0)
                self.cell.add(gdspy.boolean(gds_rect_neg, gds_rect_neg, "or", layer=layer+neg_layer_offset))    #max_points = 199 here...
                self._layer_metals[layer+neg_layer_offset] = gds_rect_neg

            # positive layer
            if self.export_type in ("positive", "all"): 
                self.cell.add(gdspy.boolean(gds_metal, gds_metal, "or", layer=layer))    #max_points = 199 here...
                self._layer_metals[layer] = gds_metal

        # fuse layers for a single-layer design (GND, metal)
        if (len(all_layers)==2) and (self.export_type in ["positive", "negative"]):
            if self.export_type=="positive":
                self.add_boolean_layer(0, 0, "and", output_layer=0)
            if self.export_type=="negative":
                self.add_boolean_layer(11, 12, "and", output_layer=0)
                self.cell.remove_polygons(lambda pts, layer, datatype: layer == 11 or layer == 12)
        
        # layer exports (works best when export_type=="All")
        if self.export_layers!=None:
            layer_list = list(self.cell.get_layers())
            # assert self.export_layers in layer_list, "Cohsen export layers are not present in the design. Choose again."
            if isinstance(self.export_layers, list):
                assert set(self.export_layers).issubset(layer_list), "Chosen export layers are not present in the design."
                to_delete = list(set(layer_list) - set(self.export_layers))
                if isinstance(to_delete, list):
                    for i in list(to_delete):
                        self.cell.remove_polygons(lambda pts, layer, datatype: layer==i)
                else:
                    self.cell.remove_polygons(lambda pts, layer, datatype: layer==to_delete)
            else:
                assert self.export_layers in layer_list, "Chosen export layer is not present in the design."
                to_export = []
                to_export.append(int(self.export_layers))
                to_delete = list(set(layer_list) - set(to_export))
                self.cell.remove_polygons(lambda pts, layer, datatype: layer==to_delete)


    def add_boolean_layer(self, layer1_ind, layer2_ind, operation, output_layer=None):
        assert operation in ["and", "or", "xor", "not"], "Operation must be: \"and\", \"or\", \"xor\", \"not\""
        if output_layer == None:
            output_layer = max([x for x in self._layer_metals]) + 1

        new_metal = gdspy.boolean(self._layer_metals[layer1_ind], self._layer_metals[layer2_ind], operation, layer=output_layer, precision=1e-6, max_points=0)
        if self.smooth_radius > 0:
            new_metal = self._round_corners(new_metal, output_layer)

        new_metal = gdspy.boolean(new_metal, new_metal, "or", layer=output_layer)    #max_points = 199 here...

        self.cell.add(new_metal)
        self._layer_metals[output_layer] = new_metal
        return output_layer
    
    # add a text label (default to bottom left)
    def add_text(self, text_label="", layer=0, size=800, position=None):
        """
        Add a text label to the gds export on a given layer at a given position.

        Inputs:
            layer       - layer number to export the text to (if none, write to layer 0)
            size        - text size
            position    - tuple containing position (normalised to 1);
                          e.g. (0,0) is bottom left, (1,1) is top right
            action      - perform a boolean operation on the layer the text is added to ('add', 'subtract')
        """
        assert isinstance(text_label, str), "Please pass a string as the text label."
        assert isinstance(position, (tuple, NoneType)), "Please pass the position as an (x,y) tuple"
        if isinstance(position, tuple):
            for i in position: 
                assert 0 <= i <= 1, "Ensure the positions are normalised to 1 (i.e. (0,0) is bottom left, (1,1) is top right)"

        layer_list = list(self.cell.get_layers())
        if (layer in layer_list) and (layer!=0):
            print("Warning: text is being added with no action argument to a (non-ground-plane) layer already containing shapes. Consider changing the layer.")

        # default label
        if text_label=="": 
            t = datetime.now().strftime("%Y")
            text_gds = f"Made with SQDMetal {t}." 
        else: text_gds = text_label

        # set default position (bottom left) or input position (normalised to 1)
        unit_conv = self.unit_conv
        default_pos = 0.48
        if position==None: text_position = ((self.cx-default_pos*self.sx)*unit_conv, (self.cy-default_pos*self.sy)*unit_conv)
        else: text_position = ((self.cx+(position[0] - 0.5)*self.sx)*unit_conv, (self.cy+(position[1] - 0.5)*self.sy)*unit_conv)
        
        # subtract text from ground plane
        if (self.export_type in ['all', 'positive']) and (layer==0):
            text = gdspy.Text(text_gds, size, text_position, layer=999)
            self.cell.add(text)
            self._layer_metals[999] = text
            self.add_boolean_layer(999, 0, "xor", output_layer=998)
            self.cell.remove_polygons(lambda pts, layer, datatype: layer==999 or layer==0)
            self.add_boolean_layer(998, 998, "or", output_layer=0)
            self.add_boolean_layer(0, 1, 'or', output_layer=0)
            self.cell.remove_polygons(lambda pts, layer, datatype: layer==998 or layer==1)
        # add to ground plane
        elif (self.export_type=='negative') and (layer==0):
            text = gdspy.Text(text_gds, size, text_position, layer=999)
            self.cell.add(text)
            self._layer_metals[999] = text
            self.add_boolean_layer(999, 0, "or", output_layer=0)
            self.cell.remove_polygons(lambda pts, layer, datatype: layer==999)
        # add as new layer
        elif layer!=0:
            text = gdspy.Text(text_gds, size, text_position, layer=layer)
            self.cell.add(text)
            self._layer_metals[layer] = text

    def export(self, file_name):
        self.lib.write_gds(file_name)
