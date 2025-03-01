import binaryninja
import math
import svd2py
from binaryninja import BinaryView, Type, StructureBuilder, Symbol, SymbolType, SegmentFlag, SectionSemantics, \
    StructureType, StructureVariant, StructureMember, Settings

BYTE_SIZE = 8


def import_svd(bv: BinaryView):
    file_path = binaryninja.get_open_filename_input('SVD File')
    if file_path is None: return
    binaryninja.log_info(f'parsing svd file... {file_path}')
    parser = svd2py.SvdParser()
    result = parser.convert(file_path)
    assert result['device'] is not None
    device = result['device']
    device_name: str = device['name']
    binaryninja.log_info(f'parsing device... {device_name}')
    peripherals = device['peripherals']['peripheral']

    show_comments = Settings().get_bool("SVDMapper.enableComments")
    structure_bitfields = Settings().get_bool("SVDMapper.enableBitfieldStructuring")

    for peripheral in peripherals:
        per_name: str = peripheral['name']
        per_desc: str = peripheral['description']
        per_base_addr: int = peripheral['baseAddress']
        per_struct = StructureBuilder.create()

        per_registers = peripheral['registers']['register']
        for register in per_registers:
            reg_name: str = register['name']
            reg_desc: str = register['description']
            reg_addr_offset: int = register['addressOffset']
            reg_size: int = register['size']
            reg_size_b = int(reg_size / BYTE_SIZE)
            reg_addr = per_base_addr + reg_addr_offset
            reg_struct = StructureBuilder.create(width=reg_size_b)

            reg_fields = register['fields']['field']
            for field in reg_fields:
                field_name: str = field['name']
                field_lsb: int = field['lsb']
                field_msb: int = field['msb']
                field_lsb_b: float = field_lsb / BYTE_SIZE
                field_msb_b: float = field_msb / BYTE_SIZE

                # If the field is byte aligned we can add a field to the register struct.
                if field_lsb_b.is_integer() and field_msb_b.is_integer():
                    # Insert named struct field.
                    # TODO: Check if struct field is overlapping existing struct field. (Can this even happen?)
                    field_bounds: tuple[int, int] = (int(field_lsb_b), int(field_msb_b))
                    reg_struct.insert(field_bounds[0], Type.int((field_bounds[1] + 1) - field_bounds[0], False),
                                      field_name)
                elif structure_bitfields:
                    # Only structure bitfields if setting is enabled.
                    # TODO: This bugs out for n fields there will be n bytes padding at the front of the union
                    field_bounds: tuple[int, int] = (math.floor(field_lsb_b), math.ceil(field_msb_b))
                    field_addr = reg_addr + field_bounds[0]
                    if show_comments:
                        bv.set_comment_at(field_addr, f'{field_name} {field_msb}:{field_lsb}')
                    # The bitfield will be use the field bounds as we cannot address bits as size
                    bitfield_ty = Type.int((field_bounds[1] + 1) - field_bounds[0], False)
                    bitfield_member = StructureMember(bitfield_ty, field_name, field_bounds[0])
                    # Create or update the bitfield union with new bitfield
                    existing_bitfield = reg_struct.member_at_offset(field_bounds[0])
                    if existing_bitfield is None:
                        reg_struct.insert(field_bounds[0], Type.union([bitfield_member]), overwrite_existing=False)
                    elif isinstance(existing_bitfield.type,
                                    StructureType) and existing_bitfield.type.type is StructureVariant.UnionStructureType:
                        bitfield_members = existing_bitfield.type.members
                        bitfield_members.append(bitfield_member)
                        reg_struct.insert(existing_bitfield.offset, Type.union(bitfield_members))

            # TODO: This is displayed really poorly
            # Add the register description as a comment
            if show_comments:
                bv.set_comment_at(reg_addr, reg_desc.splitlines()[0])
            # Define the register type in the binary view.
            reg_struct_ty = Type.structure_type(reg_struct)
            bv.define_user_type(f'{per_name}_{reg_name}', reg_struct_ty)
            # Add the register to the peripheral type
            per_struct.insert(reg_addr_offset, bv.get_type_by_name(f'{per_name}_{reg_name}'), reg_name,
                              overwrite_existing=False)

        # Get the peripheral memory range
        per_size = 0
        per_addr_blocks = peripheral['addressBlock']
        for addr_block in per_addr_blocks:
            ablk_offset: int = addr_block['offset']
            ablk_size: int = addr_block['size']
            per_size += (ablk_offset - per_size) + ablk_size

        if per_size < per_struct.width:
            binaryninja.log_warn(f"peripheral {per_name} @ {per_base_addr} size is less than struct size... adjusting size to fit struct")
            per_size = per_struct.width

        # Add entire peripheral range
        bv.add_user_segment(per_base_addr, per_size, 0, 0, SegmentFlag.SegmentReadable | SegmentFlag.SegmentWritable)
        bv.add_user_section(per_name, per_base_addr, per_size, SectionSemantics.ReadWriteDataSectionSemantics)
        bv.memory_map.add_memory_region(per_name, per_base_addr, bytearray(per_size))

        # Add the peripheral description as a comment
        if show_comments:
            bv.set_comment_at(per_base_addr, per_desc)
        # Define the peripheral type and data var in the binary view.
        per_struct_ty = Type.structure_type(per_struct)
        bv.define_user_type(per_name, per_struct_ty)
        bv.define_user_symbol(Symbol(SymbolType.ImportedDataSymbol, per_base_addr, per_name))
        bv.define_user_data_var(per_base_addr, bv.get_type_by_name(per_name), per_name)


settings = Settings()

bf_title = "Enable Bitfield Structuring"
bf_description = "Bitfields will be structured as unions"
bf_properties = f'{{"title" : "{bf_title}", "description" : "{bf_description}", "type" : "boolean", "default" : false}}'

comment_title = "Enable Comment Creation"
comment_description = "Create comments from the SVD field descriptions"
comment_properties = f'{{"title" : "{comment_title}", "description" : "{comment_description}", "type" : "boolean", "default" : true}}'

settings.register_group("SVDMapper", "SVD Mapper")
settings.register_setting("SVDMapper.enableBitfieldStructuring", bf_properties)
settings.register_setting("SVDMapper.enableComments", comment_properties)

binaryninja.PluginCommand.register(
    "Import SVD Info",
    "Maps SVD peripherals into the binary view as new segments",
    import_svd
)
