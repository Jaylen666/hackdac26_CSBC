module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire key_chk,
    input wire key_vld_o,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (key_vld_o |-> !$isunknown(key_chk) && $stable(key_chk)));

endmodule


bind keymgr_input_checks sva_checker sva_checker_inst (.*);
