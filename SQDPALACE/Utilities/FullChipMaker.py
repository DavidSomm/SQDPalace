from qiskit_metal import designs
from SQDMetal.Utilities.Materials import Material 
from SQDMetal.Utilities.MakeGDS import MakeGDS
from SQDMetal.Utilities.CpwParams import CpwParams
from SQDMetal.Utilities.QUtilities import QUtilities
import numpy as np
import os
from datetime import datetime

class MultiDieChip:
    @staticmethod
    def make_resonator_chip(export_filename, 
                            export_path="", 
                            export_type="all",
                            frequency_range=(6e9, 7e9), 
                            num_resonators=5, 
                            cpw_width="9um", 
                            coupling_gap="20um", 
                            tl_y="0um",
                            res_vertical="1500um",
                            lp_to_res="300um",
                            lp_inset="0um",
                            lp_dimension="600um",
                            lp_taper="300um",
                            substrate_material="silicon", 
                            substrate_thickness="0.5mm", 
                            film_thickness="100nm",
                            chip_dimension=("20mm", "20mm"), 
                            chip_border="500um",     
                            die_dimension=("7.1mm", "4.4mm"),    
                            die_num=[1,1],
                            fill_chip=True,
                            markers_on=True,
                            text_label="",
                            text_size=600,
                            text_position=None,
                            print_all_infos=True
                            ):
        '''
        Creates a `.gds` full-wafer layout file for a simple coplanar waveguide $\lambda/4$ resonator chip containing a number of resonators (usually 5) capacitively coupled to a transmission line.

        Inputs:
            - export_filename - Filename for gds export (e.g. "test")
            - export_path - Path for export (e.g. 'exports'); the file will then be output to /exports/test.gds
            - export_type - (Defaults to "all") Export type for lithography as per `MakeGDS` (options: "all", "positive", "negative")
            - frequency_range - (Defaults to (6e9, 7e9)) Tuple containing minimum and maximum resonator frequencies in Hz
            - num_resonators - (Defaults to 5) Number of resonators per die
            - cpw_width - (Defaults to "9um") Width of the central trace on the feedline and resonators. The gap will be automatically calculated for 50 Ohm impedance based on the `substrate_material`
            - coupling_gap - (Defaults to "20um") Amount of ground plane in the coupling gap between the feedline and the resonator
            - tl_y - (Defaults to "0um") The die-relative y-value for the main straight of the feedline (NOTE: currently only "0um" is supported)
            - res_vertical - (Defaults to "1500um") Vertical length of resonator meanders
            - lp_to_res - (Defaults to "300um") Minimum distance between the launchpad taper and the coupling length of the left-most resonator
            - lp_inset - (Defaults to "0um") Inset of the launchpads along the x-axis relative to the die boundaries
            - lp_dimension - (Defaults to "600um") Width of the launchpads' conductive centre pad (the launchpad gap scales accordingly)
            - lp_taper - (Defaults to "300um") Length of the taper from launchpad to feedline
            - substrate_material - (Defaults to "silicon") Substrate material (currently only "silicon" and "sapphire" are supported)
            - substrate_thickness - (Defaults to "0.5mm") Substrate thickness
            - film_thickness - (Defaults to "100nm") Film thickness
            - chip_dimension - (Defaults to ("20mm", "20mm")) Dimensions of the chip as an (x, y) Tuple
            - chip_border - (Defaults to "500um") Chip border to leave un-patterned
            - die_dimension - (Defaults to ("7.1mm", "4.4mm")) Dimensions of the die as an (x, y) Tuple
            - die_num - (Defaults to [1, 1])) Die layout in [x, y] as a length-2 list
            - fill_chip - (Defaults to True) Boolean to choose whether the full chip is automatically populated with dies (over-rides die_num if True)
            - markers_on - (Defaults to True) Print dicing markers on export
            - text_label - (Optional) Text label to print on chip
            - text_size - (Defaults to 600) Text size
            - text_position - (Optional) Tuple of text label location as normalised (x, y) (e.g. (0.1, 0.9) will place the text label 1/10th of the way along the chip in the x-direction, and 9/10ths of the way up the chip in the y-direction)
            - print_all_infos - (Defaults to True) Choose whether to print info as the `.gds` is being generated
        
        Outputs:
            - design - Qiskit Metal design object for the generated chip
        '''

        # TODO: add option for scaling of resonator dimensions compared to feedline dimensions (i.e. so the feedline can be larger)

        # TODO: add support for other `tl_y` values

        t = datetime.now().strftime("%Y%m%d_%H%M")  # add timestamp to export

        print(f"{t}\nBuilding chip \"{export_filename}\" with the following options:\n")
        print(locals())
        print('\n\n')

        # assign other values form input parameters
        substrate = Material(substrate_material)
        er = substrate.permittivity

        # initialise and clear design
        design = designs.DesignPlanar(
            metadata={}, overwrite_enabled=True, enable_renderers=True
        )
        design.delete_all_components()

        # set chip dimension and material
        design.chips.main.size.size_x = chip_dimension[0]
        design.chips.main.size.size_y = chip_dimension[1]
        design.chips.main.size.size_z = '-' + substrate_thickness
        design.chips.main.material = [substrate_material]

        # enact
        design.chips.main

        # initialise CpwParams object for calculations
        c = CpwParams(rel_permittivity=er, dielectric_thickness=QUtilities.parse_value_length(substrate_thickness))
        cpw_params = c.fromQDesign(design=design)

        # calculate gap from specified width for 50 Ohm impedence
        gap = str(f"{c.get_gap_from_width(trace_width=QUtilities.parse_value_length(cpw_width)) * 1e6:.2f}um")

        # calculate gap width ratio
        width_to_gap_ratio = QUtilities.parse_value_length(cpw_width) / QUtilities.parse_value_length(gap)

        # calculate cpw impedance
        cpw_impedance = CpwParams.calc_impedance(tr_wid=QUtilities.parse_value_length(cpw_width), tr_gap=QUtilities.parse_value_length(gap), er=er, h=QUtilities.parse_value_length(substrate_thickness))

        # calculate number of die in x and y if fill_chip is True
        if fill_chip:
            die_num[0] = int(
                (QUtilities.parse_value_length(chip_dimension[0]) - QUtilities.parse_value_length(chip_border))
                // QUtilities.parse_value_length(die_dimension[0])
            )
            die_num[1] = int(
                (QUtilities.parse_value_length(chip_dimension[1]) - QUtilities.parse_value_length(chip_border))
                // QUtilities.parse_value_length(die_dimension[1])
            )
        total_die_num = die_num[0] * die_num[1] # total number of die

        # list of tuples containing die centre coordinates
        die_coords = QUtilities.calc_die_coords(chip_dimension, die_dimension, die_num)

        # calculate frequencies
        freq_list = np.linspace(frequency_range[0], frequency_range[1], num_resonators, endpoint=True)

        # calculate total launchpad width (from edge of die)
        lp_extent = (
            QUtilities.parse_value_length(lp_inset)
            + QUtilities.parse_value_length(lp_dimension) / width_to_gap_ratio
            + QUtilities.parse_value_length(lp_dimension)
            + QUtilities.parse_value_length(lp_taper)
            + (25 * 1e-6)
        )

        print('CPW properties:')
        print(f'\tCPW gap: {gap}')
        print(f'\tCPW width: {cpw_width}')
        print(f'\tCPW impedance: {cpw_impedance:.2f} Ohm')
        print('\nLaunchpad properties:')
        print(f'\tLaunchpad extent: {lp_extent * 1e6:.2f}um\n\n')

        print('~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~\n')

        # place and store launchpads, resonators, transmission lines
        launchpads = []
        resonators_list, resonator_vals, resonator_names, capacitances, inductances = [], [], [], [], []
        transmission_lines = []

        # loop through dies
        for i, origin in enumerate(die_coords):
            
            # draw launchpads
            launchpads.append(
                QUtilities.place_launchpads(
                    design=design,
                    cpw_gap=gap,
                    cpw_width=cpw_width,
                    die_origin=origin,
                    die_dimension=die_dimension,
                    die_number=(i + 1),
                    dimension=lp_dimension,
                    inset=lp_inset,
                    taper=lp_taper,
                )
            )

            # draw resonators
            resonators, resonator_val, resonator_name, capacitance, inductance = QUtilities.place_resonators_hanger(design=design,
                gap=gap,
                width=cpw_width,
                num_resonators=num_resonators,
                frequencies=freq_list,
                die_origin=origin,
                die_dimension=die_dimension,
                die_index=i,
                film_thickness=film_thickness,
                launchpad_extent=lp_extent,
                coupling_gap=coupling_gap,
                transmission_line_y=tl_y,
                launchpad_to_res="250um",
                min_res_gap="50um",
                LC_calculations=True,
                print_statements=print_all_infos
                )
            
            resonators_list.append(resonators)
            resonator_vals.append(resonator_val)
            resonator_names.append(resonator_name)
            capacitances.append(capacitance)
            inductances.append(inductance)

            # draw transmission lines
            transmission_lines.append(QUtilities.place_transmission_line_from_launchpads(
                design=design,
                tl_y=tl_y,
                gap=gap,
                width=cpw_width,
                die_index=i))

            # draw markers
            if markers_on: 
                QUtilities.place_markers(design=design, die_origin=origin, die_dim=die_dimension)

        print('\n\n~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~\n')

        # setup GDS export (positive)
        gds_export = MakeGDS(design, export_type=export_type)

        # add text label
        if text_position==None:
            gds_export.add_text(text_label=text_label, size=text_size, position=(0.05, 0.93))
        else:
            gds_export.add_text(text_label=text_label, size=text_size, position=text_position)

        # setup export path based on user inputs
        if export_path=="":
            full_export_path = os.path.join(f"{export_filename}_{t}.gds")
        else:
            full_export_path = os.path.join(export_path, f"{export_filename}_{t}.gds")

        # do export
        gds_export.export(full_export_path)

        print(f"Exported at {full_export_path}")

        return design