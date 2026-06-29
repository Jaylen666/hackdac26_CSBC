module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire cdi_sel,
    input wire sw_binding,
    input wire RndCnstCdi,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) ((cdi_sel != 2'd0) && (cdi_sel != 2'd1)) |-> (sw_binding == RndCnstCdi));

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
