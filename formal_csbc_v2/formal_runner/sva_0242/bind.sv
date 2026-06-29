module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire init_o,
    input wire init_i,
    input wire en_i,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) ($rose(init_o) |-> $past(init_i) && $past(en_i)));

endmodule


bind keymgr_ctrl sva_checker sva_checker_inst (.*);
